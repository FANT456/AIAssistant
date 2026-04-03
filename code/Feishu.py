import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
TOKEN_CACHE_FILE = ROOT_DIR / "feishu_tokens.json"
TOKEN_CACHE_BUFFER_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 20
TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
CALENDAR_LIST_URL = "https://open.feishu.cn/open-apis/calendar/v4/calendars"
CALENDAR_EVENTS_URL = "https://open.feishu.cn/open-apis/calendar/v4/calendars/{calendar_id}/events"

def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def parse_timestamp(value: str, field_name: str) -> int:
    raw = (value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    if not raw.isdigit():
        raise ValueError(f"{field_name} 必须是秒级时间戳")
    return int(raw)

class Feishu:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        calendar_id: str = "",
        summary: str = "",
        start_time: str = "",
        end_time: str = "",
        cache_file: Path = TOKEN_CACHE_FILE,
        request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.tenant_access_token = None
        self.app_id = app_id
        self.app_secret = app_secret
        self.preferred_calendar_id = calendar_id
        self.summary = summary
        self.start_time = start_time
        self.end_time = end_time
        self.cache_file = cache_file
        self.request_timeout_seconds = request_timeout_seconds

    def validate_required_config(self) -> Optional[Exception]:
        if not self.app_id:
            return Exception("APP_ID is required")
        if not self.app_secret:
            return Exception("APP_SECRET is required")
        return None

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[Exception]]:
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=payload,
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return {}, Exception(f"HTTP 请求失败: {exc}")

        try:
            result = response.json()
        except ValueError:
            return {}, Exception(f"接口返回了非 JSON 内容: {response.text}")

        if result.get("code", 0) != 0:  # 非0表示失败
            message = result.get("msg", "unknown error")
            log_id = response.headers.get("X-Tt-Logid", "")
            return {}, Exception(f"code={result.get('code')}; msg={message}; log_id={log_id}")

        return result, None

    def _load_cached_tenant_token(self) -> Optional[str]:
        if not self.cache_file.exists():
            return None

        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        expires_at = raw.get("expires_at")
        tenant_access_token = raw.get("tenant_access_token")
        if not tenant_access_token or not expires_at:
            return None

        if int(expires_at) <= int(time.time()) + TOKEN_CACHE_BUFFER_SECONDS:
            return None

        return str(tenant_access_token)

    def _save_cached_tenant_token(self, tenant_access_token: str, expires_in_seconds: int) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        expires_at = int(time.time()) + max(expires_in_seconds, 0)
        self.cache_file.write_text(
            json.dumps(
                {
                    "tenant_access_token": tenant_access_token,
                    "expires_at": expires_at,
                    "updated_at": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _auth_headers(tenant_access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def get_tenant_access_token(self) -> Tuple[str, Optional[Exception]]:
        """获取 tenant_access_token，优先使用本地缓存。"""
        cached_token = self._load_cached_tenant_token()
        if cached_token:
            print(f"[缓存] 使用本地 tenant_access_token: {mask_secret(cached_token)}")
            return cached_token, None

        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}

        print(f"[请求] 获取 tenant_access_token: {TENANT_TOKEN_URL}")
        print(f"[请求] APP_ID={mask_secret(self.app_id)} APP_SECRET={mask_secret(self.app_secret)}")

        result, err = self._request_json("POST", TENANT_TOKEN_URL, headers=headers, payload=payload)
        if err:
            return "", Exception(f"获取 tenant_access_token 失败: {err}")

        tenant_access_token = str(result.get("tenant_access_token", "") or "")
        expires_in_seconds = int(result.get("expire", result.get("expires_in", 7200)) or 7200)
        if not tenant_access_token:
            return "", Exception("返回结果中未包含 tenant_access_token")

        self._save_cached_tenant_token(tenant_access_token, expires_in_seconds)
        return tenant_access_token, None

    def get_calendar_list(self, tenant_access_token: str) -> Tuple[List[Dict[str, Any]], Optional[Exception]]:
        """获取日历列表。"""
        headers = self._auth_headers(tenant_access_token)

        calendars: List[Dict[str, Any]] = []
        page_token = ""
        has_more = True

        while has_more:
            params: Dict[str, Any] = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token

            print(f"[请求] 查询日历列表: {CALENDAR_LIST_URL}; params={params}")
            result, err = self._request_json("GET", CALENDAR_LIST_URL, headers=headers, params=params)
            if err:
                return [], Exception(f"获取日历列表失败: {err}")

            data = result.get("data", {})
            calendars.extend(data.get("calendar_list", []))
            has_more = bool(data.get("has_more", False))
            page_token = str(data.get("page_token", "") or "")

        return calendars, None

    def get_events(self, tenant_access_token: str, calendar_id: str) -> Tuple[List[Dict[str, Any]], Optional[Exception]]:
        """获取指定日历的日程列表。"""
        headers = self._auth_headers(self.tenant_access_token)
        url = CALENDAR_EVENTS_URL.format(calendar_id=calendar_id)

        events: List[Dict[str, Any]] = []
        page_token = ""
        has_more = True

        while has_more:
            params: Dict[str, Any] = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token

            print(f"[请求] 查询日程列表: {url}; params={params}")
            result, err = self._request_json("GET", url, headers=headers, params=params)
            if err:
                return [], Exception(f"获取日程列表失败: {err}")

            data = result.get("data", {})
            items = data.get("items") or data.get("event_list") or []
            events.extend(items)
            has_more = bool(data.get("has_more", False))
            page_token = str(data.get("page_token", "") or "")

        return events, None

    def create_event(self, schedule_payload: str) -> Tuple[Dict[str, Any], Optional[Exception]]:
        """创建日程。"""
        token, err = self.get_tenant_access_token()
        if err:
            return {}, Exception(f"获取 tenant_access_token 失败: {err}")
        self.tenant_access_token = token

        tz = timezone(timedelta(hours=8))  # UTC+8
        dt = datetime.strptime(schedule_payload["start_time"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        self.start_time=str(int(dt.timestamp()))

        dt = datetime.strptime(schedule_payload["end_time"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        self.end_time = str(int(dt.timestamp()))

        self.summary=schedule_payload["title"];
        start_timestamp = parse_timestamp(self.start_time, "start_time")
        end_timestamp = parse_timestamp(self.end_time, "end_time")
        if start_timestamp >= end_timestamp:
            return {}, Exception("START_TIME 必须早于 END_TIME")

        url = CALENDAR_EVENTS_URL.format(calendar_id=self.preferred_calendar_id)
        headers = self._auth_headers(self.tenant_access_token)
        payload = {
            "summary": self.summary,
            "start_time": {
                "date": "",
                "timestamp": str(start_timestamp),
                "timezone": "Asia/Shanghai",
            },
            "end_time": {
                "date": "",
                "timestamp": str(end_timestamp),
                "timezone": "Asia/Shanghai",
            },
        }

        print(f"[请求] 创建日程: {url}")
        print(f"[请求] 日程标题: {self.summary}")

        result, err = self._request_json("POST", url, headers=headers, payload=payload)
        if err:
            return {}, Exception(f"创建日程失败: {err}")

        return result.get("data", {}).get("event", {}), None

    def _choose_target_calendar(self, calendars: List[Dict[str, Any]]) -> Tuple[str, Optional[Exception]]:
        if self.preferred_calendar_id:
            for calendar in calendars:
                if calendar.get("calendar_id") == self.preferred_calendar_id:
                    return self.preferred_calendar_id, None
            return "", Exception(f"未在日历列表中找到 CALENDAR_ID={self.preferred_calendar_id}")

        for calendar in calendars:
            if calendar.get("role") in ["writer", "owner"] and not calendar.get("is_deleted", False):
                return str(calendar.get("calendar_id", "") or ""), None

        return "", Exception("没有找到可写入的日历")

    def run(self) -> int:
        config_error = self.validate_required_config()
        if config_error:
            print(f"[错误] {config_error}", file=sys.stderr)
            return 1

        self.tenant_access_token, err = self.get_tenant_access_token()
        if err:
            print(f"[错误] {err}", file=sys.stderr)
            return 1

        calendars, err = self.get_calendar_list(tenant_access_token)
        if err:
            print(f"[错误] {err}", file=sys.stderr)
            return 1
        if not calendars:
            print("[错误] 未查询到任何日历", file=sys.stderr)
            return 1

        target_calendar_id, err = self._choose_target_calendar(calendars)
        if err:
            print(f"[错误] {err}", file=sys.stderr)
            return 1

        print(f"[日历] 当前目标日历 ID: {target_calendar_id}")

        events, err = self.get_events(tenant_access_token, target_calendar_id)
        if err:
            print(f"[错误] {err}", file=sys.stderr)
            return 1

        print(f"[日程] 当前日历已有 {len(events)} 条日程")

        has_any_creation_input = any([self.summary, self.start_time, self.end_time])
        has_all_creation_input = all([self.summary, self.start_time, self.end_time])
        if has_any_creation_input and not has_all_creation_input:
            print("[错误] 创建日程时必须同时提供 SUMMARY、START_TIME、END_TIME", file=sys.stderr)
            return 1

        if has_all_creation_input:
            event, err = self.create_event(tenant_access_token, target_calendar_id)
            if err:
                print(f"[错误] {err}", file=sys.stderr)
                return 1

            print("[成功] 日程创建成功")
            print(
                json.dumps(
                    {
                        "event_id": event.get("event_id"),
                        "summary": event.get("summary", self.summary),
                        "calendar_id": target_calendar_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("[提示] 未提供 SUMMARY、START_TIME、END_TIME，当前仅完成 token 获取、日历查询和日程列表查询")

        return 0
