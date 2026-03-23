"""
飞书日历客户端：负责获取 user_access_token 并创建日历事件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any

import requests

from config import (
    DEFAULT_TIMEZONE,
    FEISHU_API_CALENDARS,
    FEISHU_API_CALENDAR_EVENT,
    FEISHU_API_USER_TOKEN,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_AUTH_CODE,
    FEISHU_CALENDAR_ID,
    FEISHU_CODE_VERIFIER,
    FEISHU_REFRESH_TOKEN,
    FEISHU_REDIRECT_URI,
    FEISHU_TOKEN_REFRESH_SKEW_SECONDS,
    FEISHU_TOKEN_STORE_PATH,
    FEISHU_USER_ACCESS_TOKEN,
)
from token_store import FeishuTokenStore, StoredFeishuToken, TokenStoreError


class FeishuCalendarError(RuntimeError):
    """飞书日历调用失败。"""


@dataclass
class FeishuCreateResult:
    success: bool
    event_id: str = ""
    calendar_id: str = ""
    raw_response: dict | None = None
    error_message: str = ""


class FeishuCalendarClient:
    """飞书 Calendar API 客户端。"""

    def __init__(
        self,
        app_id: str = FEISHU_APP_ID,
        app_secret: str = FEISHU_APP_SECRET,
        user_access_token: str = FEISHU_USER_ACCESS_TOKEN,
        refresh_token: str = FEISHU_REFRESH_TOKEN,
        auth_code: str = FEISHU_AUTH_CODE,
        redirect_uri: str = FEISHU_REDIRECT_URI,
        code_verifier: str = FEISHU_CODE_VERIFIER,
        calendar_id: str = FEISHU_CALENDAR_ID,
        token_store: FeishuTokenStore | None = None,
        refresh_skew_seconds: int = FEISHU_TOKEN_REFRESH_SKEW_SECONDS,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.user_access_token = user_access_token
        self.refresh_token = refresh_token
        self.auth_code = auth_code
        self.redirect_uri = redirect_uri
        self.code_verifier = code_verifier
        self.calendar_id = calendar_id
        self.token_store = token_store or FeishuTokenStore(FEISHU_TOKEN_STORE_PATH)
        self.refresh_skew_seconds = max(int(refresh_skew_seconds), 0)
        self.resolved_calendar_id = ""
        self.cached_user_access_token = ""
        self.cached_token_record: StoredFeishuToken | None = None
        self._token_lock = threading.RLock()

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret and (self.user_access_token or self.refresh_token or self.auth_code or self._has_local_token_state()))

    def has_valid_configuration(self) -> bool:
        return self.is_configured() and not self.get_invalid_config_issues()

    def get_missing_config_fields(self) -> list[str]:
        missing = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.user_access_token and not self.refresh_token and not self.auth_code and not self._has_local_token_state():
            missing.append("FEISHU_USER_ACCESS_TOKEN / FEISHU_REFRESH_TOKEN / FEISHU_AUTH_CODE")
        return missing

    def get_invalid_config_issues(self) -> list[str]:
        issues = []
        has_local_token_state = self._has_local_token_state()

        if self.app_id and _looks_like_placeholder(self.app_id):
            issues.append("FEISHU_APP_ID 仍是模板占位值")
        if self.app_secret and _looks_like_placeholder(self.app_secret):
            issues.append("FEISHU_APP_SECRET 仍是模板占位值")
        if self.user_access_token and _looks_like_placeholder(self.user_access_token) and not has_local_token_state:
            issues.append("FEISHU_USER_ACCESS_TOKEN 仍是模板占位值")
        if self.refresh_token and _looks_like_placeholder(self.refresh_token) and not has_local_token_state:
            issues.append("FEISHU_REFRESH_TOKEN 仍是模板占位值")
        if self.auth_code and _looks_like_placeholder(self.auth_code) and not has_local_token_state:
            issues.append("FEISHU_AUTH_CODE 仍是模板占位值")
        if self.calendar_id and _looks_like_placeholder(self.calendar_id):
            issues.append("FEISHU_CALENDAR_ID 仍是模板占位值")

        if self.app_secret and self.app_secret.endswith(".env.example"):
            issues.append("FEISHU_APP_SECRET 看起来误填成了模板文件名/占位内容")

        if self.calendar_id and not _looks_like_placeholder(self.calendar_id) and len(self.calendar_id.strip()) < 8:
            issues.append("FEISHU_CALENDAR_ID 长度异常，请确认填写的是实际 calendar_id")

        return issues

    def validate_configuration(self):
        if not self.is_configured():
            missing = ", ".join(self.get_missing_config_fields())
            raise FeishuCalendarError(f"缺少飞书配置：{missing}")

        issues = self.get_invalid_config_issues()
        if issues:
            raise FeishuCalendarError("飞书配置无效：" + "；".join(issues))

    def _build_auth_headers(self, user_access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {user_access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

    def _has_local_token_state(self) -> bool:
        try:
            token = self._load_stored_token(force_reload=not bool(self.cached_token_record))
        except FeishuCalendarError:
            return False
        return bool(token and (token.access_token or token.refresh_token))

    def _load_stored_token(self, force_reload: bool = False) -> StoredFeishuToken | None:
        if self.cached_token_record is not None and not force_reload:
            return self.cached_token_record

        try:
            token = self.token_store.load_token()
        except TokenStoreError as exc:
            raise FeishuCalendarError(f"读取本地飞书 token 失败: {exc}") from exc

        self.cached_token_record = token
        if token and token.access_token:
            self.cached_user_access_token = token.access_token
        if token and token.refresh_token:
            self.refresh_token = token.refresh_token
        return token

    def _save_stored_token(self, token: StoredFeishuToken):
        try:
            self.token_store.save_token(token)
        except TokenStoreError as exc:
            raise FeishuCalendarError(f"保存本地飞书 token 失败: {exc}") from exc

        self.cached_token_record = token
        self.cached_user_access_token = token.access_token
        if token.refresh_token:
            self.refresh_token = token.refresh_token

    def _is_access_token_usable(self, token: StoredFeishuToken | None) -> bool:
        if not token or not token.access_token:
            return False
        if token.expires_at is None:
            return True
        return _now_ts() < (token.expires_at - self.refresh_skew_seconds)

    def _can_refresh_with_token(self, token: StoredFeishuToken | None) -> bool:
        if not token or not token.refresh_token:
            return False
        if token.refresh_expires_at is None:
            return True
        return _now_ts() < (token.refresh_expires_at - self.refresh_skew_seconds)

    def _can_refresh(self) -> bool:
        try:
            token = self._load_stored_token()
        except FeishuCalendarError:
            token = None
        return self._can_refresh_with_token(token) or bool(self.refresh_token) or bool(self.auth_code)

    def _is_configured_calendar_id(self) -> bool:
        return bool(self.calendar_id and not _looks_like_placeholder(self.calendar_id) and len(self.calendar_id.strip()) >= 8)

    def _request_token(self, grant_type: str, **extra_payload: Any) -> StoredFeishuToken:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        payload = {
            "grant_type": grant_type,
            "client_id": self.app_id,
            "client_secret": self.app_secret,
        }
        payload.update({key: value for key, value in extra_payload.items() if value})

        try:
            resp = requests.post(FEISHU_API_USER_TOKEN, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            action = "刷新 user_access_token" if grant_type == "refresh_token" else "获取 user_access_token"
            raise FeishuCalendarError(f"{action} HTTP 失败: {exc}") from exc

        data = _safe_json(resp)
        if not isinstance(data, dict):
            log_id = _extract_log_id(resp)
            action = "刷新 user_access_token" if grant_type == "refresh_token" else "获取 user_access_token"
            raise FeishuCalendarError(
                _format_error_message(
                    f"{action} 失败",
                    http_status=resp.status_code,
                    log_id=log_id,
                    details="返回不是合法 JSON",
                )
            )

        if data.get("code") != 0:
            log_id = _extract_log_id(resp, data)
            feishu_msg = _extract_feishu_message(data)
            details = _suggest_fix_for_feishu_error(data.get("code"), feishu_msg)
            action = "刷新 user_access_token" if grant_type == "refresh_token" else "获取 user_access_token"
            raise FeishuCalendarError(
                _format_error_message(
                    f"{action} 失败",
                    feishu_code=data.get("code"),
                    feishu_msg=feishu_msg,
                    http_status=resp.status_code,
                    log_id=log_id,
                    details=details,
                )
            )

        token_payload = _extract_token_payload(data)
        token = str(token_payload.get("access_token") or "").strip()
        refresh_token = str(token_payload.get("refresh_token") or "").strip()
        if not token:
            raise FeishuCalendarError("飞书未返回 user_access_token(access_token)。")

        now_ts = _now_ts()
        return StoredFeishuToken(
            access_token=token,
            refresh_token=refresh_token or self.refresh_token,
            expires_at=_expires_at_from_payload(token_payload, now_ts, ("expires_at", "expires_in", "expire_in", "expire", "access_token_expires_in")),
            refresh_expires_at=_expires_at_from_payload(token_payload, now_ts, ("refresh_expires_at", "refresh_expires_in", "refresh_expire_in", "refresh_expire", "refresh_token_expires_in")),
            obtained_at=now_ts,
            token_type=str(token_payload.get("token_type") or "Bearer").strip() or "Bearer",
        )

    def _exchange_auth_code_for_token(self) -> StoredFeishuToken:
        payload = {"code": self.auth_code}
        if self.redirect_uri:
            payload["redirect_uri"] = self.redirect_uri
        if self.code_verifier:
            payload["code_verifier"] = self.code_verifier
        return self._request_token("authorization_code", **payload)

    def _refresh_token_record(self, refresh_token: str) -> StoredFeishuToken:
        return self._request_token("refresh_token", refresh_token=refresh_token)

    def _authorized_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        retry_on_auth_error: bool = True,
    ) -> requests.Response:
        token = self.get_user_access_token()
        resp = self._send_request(method, url, self._build_auth_headers(token), params=params, json_payload=json_payload)

        if retry_on_auth_error and self._can_refresh() and _response_indicates_auth_failure(resp):
            refreshed_token = self.get_user_access_token(force_refresh=True)
            if refreshed_token != token:
                resp = self._send_request(method, url, self._build_auth_headers(refreshed_token), params=params, json_payload=json_payload)

        return resp

    def _send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> requests.Response:
        method_name = method.strip().upper()
        if method_name == "GET":
            return requests.get(url, headers=headers, params=params, timeout=30)
        if method_name == "POST":
            return requests.post(url, headers=headers, params=params, json=json_payload, timeout=30)
        raise ValueError(f"不支持的 HTTP 方法: {method}")

    def list_calendars(self, user_access_token: str | None = None, page_size: int = 500) -> list[dict[str, Any]]:
        calendars: list[dict[str, Any]] = []
        page_token = ""

        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token

            try:
                if user_access_token:
                    resp = self._send_request("GET", FEISHU_API_CALENDARS, self._build_auth_headers(user_access_token), params=params)
                else:
                    resp = self._authorized_request("GET", FEISHU_API_CALENDARS, params=params)
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise FeishuCalendarError(f"查询飞书日历列表 HTTP 失败: {exc}") from exc

            data = _safe_json(resp)
            if not isinstance(data, dict):
                log_id = _extract_log_id(resp)
                raise FeishuCalendarError(
                    _format_error_message(
                        "查询飞书日历列表失败",
                        http_status=resp.status_code,
                        log_id=log_id,
                        details="返回不是合法 JSON",
                    )
                )

            if data.get("code") != 0:
                log_id = _extract_log_id(resp, data)
                raise FeishuCalendarError(
                    _format_error_message(
                        "查询飞书日历列表失败",
                        feishu_code=data.get("code"),
                        feishu_msg=data.get("msg") or data.get("message") or "未知错误",
                        http_status=resp.status_code,
                        log_id=log_id,
                        details=_suggest_fix_for_feishu_error(data.get("code"), data.get("msg") or data.get("message") or ""),
                    )
                )

            page_calendars = data.get("data", {}).get("calendar_list")
            if page_calendars is None:
                page_calendars = data.get("data", {}).get("items")
            if page_calendars is None:
                page_calendars = data.get("data", {}).get("calendars")

            if isinstance(page_calendars, list):
                calendars.extend(page_calendars)

            has_more = bool(data.get("data", {}).get("has_more"))
            next_page_token = str(data.get("data", {}).get("page_token") or "").strip()
            if not has_more or not next_page_token:
                break
            page_token = next_page_token

        return calendars

    def get_primary_calendar(self, calendars: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not calendars:
            return None

        for calendar in calendars:
            calendar_type = str(calendar.get("type") or "").lower()
            if calendar_type == "primary" or calendar.get("is_default") or calendar.get("default") or calendar.get("is_primary"):
                return calendar

        for calendar in calendars:
            role = str(calendar.get("access_role") or calendar.get("role") or "").lower()
            if role in {"owner", "writer", "editor"}:
                return calendar

        return calendars[0]

    def resolve_calendar_id(self, force_refresh: bool = False, user_access_token: str | None = None) -> str:
        if self.resolved_calendar_id and not force_refresh:
            return self.resolved_calendar_id

        self.validate_configuration()

        if self._is_configured_calendar_id():
            self.resolved_calendar_id = self.calendar_id
            return self.resolved_calendar_id

        token = user_access_token or self.get_user_access_token()

        calendars = self.list_calendars(token)
        if not calendars:
            raise FeishuCalendarError("未查询到任何可用飞书日历，请确认应用有日历访问权限。")

        primary = self.get_primary_calendar(calendars)
        if not primary:
            raise FeishuCalendarError("未能从飞书返回的日历列表中解析出可用 calendar_id。")

        resolved_id = str(primary.get("calendar_id") or primary.get("id") or "").strip()
        if not resolved_id:
            raise FeishuCalendarError("飞书日历列表返回了记录，但未包含有效 calendar_id。")

        self.resolved_calendar_id = resolved_id
        return self.resolved_calendar_id

    def get_user_access_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            stored_token = self._load_stored_token()
            if stored_token and not force_refresh and self._is_access_token_usable(stored_token):
                self.cached_user_access_token = stored_token.access_token
                return stored_token.access_token

            if not force_refresh and self.user_access_token and not stored_token and not self.refresh_token and not self.auth_code:
                self.cached_user_access_token = self.user_access_token
                return self.cached_user_access_token

            self.validate_configuration()

            try:
                with self.token_store.file_lock():
                    stored_token = self._load_stored_token(force_reload=True)
                    if stored_token and not force_refresh and self._is_access_token_usable(stored_token):
                        self.cached_user_access_token = stored_token.access_token
                        return stored_token.access_token

                    if stored_token and self._can_refresh_with_token(stored_token):
                        refreshed = self._refresh_token_record(stored_token.refresh_token)
                        self._save_stored_token(refreshed)
                        return refreshed.access_token

                    if self.refresh_token:
                        refreshed = self._refresh_token_record(self.refresh_token)
                        self._save_stored_token(refreshed)
                        return refreshed.access_token

                    if self.auth_code:
                        exchanged = self._exchange_auth_code_for_token()
                        self._save_stored_token(exchanged)
                        return exchanged.access_token
            except TokenStoreError as exc:
                raise FeishuCalendarError(f"本地飞书 token 存储失败: {exc}") from exc

            if self.user_access_token and not force_refresh:
                self.cached_user_access_token = self.user_access_token
                return self.cached_user_access_token

            if self.user_access_token and force_refresh:
                raise FeishuCalendarError("当前只有静态 FEISHU_USER_ACCESS_TOKEN，无法自动刷新；请提供 FEISHU_REFRESH_TOKEN 或重新授权获取 FEISHU_AUTH_CODE。")

            raise FeishuCalendarError("无法获取可用的 user_access_token，请检查 FEISHU_REFRESH_TOKEN / FEISHU_AUTH_CODE 或本地 token 存储。")

    def create_event(self, schedule: dict) -> FeishuCreateResult:
        try:
            token = self.get_user_access_token()
            calendar_id = self.resolve_calendar_id(user_access_token=token)
            payload = self._build_event_payload(schedule)
            url = f"{FEISHU_API_CALENDAR_EVENT}/{calendar_id}/events"
            resp = self._authorized_request("POST", url, json_payload=payload)
            resp.raise_for_status()
            data = _safe_json(resp)

            if not isinstance(data, dict):
                log_id = _extract_log_id(resp)
                return FeishuCreateResult(
                    success=False,
                    error_message=_format_error_message(
                        "创建飞书日历事件失败",
                        http_status=resp.status_code,
                        log_id=log_id,
                        details="返回不是合法 JSON",
                    ),
                )

            if data.get("code") != 0:
                log_id = _extract_log_id(resp, data)
                return FeishuCreateResult(
                    success=False,
                    raw_response=data,
                    error_message=_format_error_message(
                        "创建飞书日历事件失败",
                        feishu_code=data.get("code"),
                        feishu_msg=data.get("msg") or data.get("message") or "未知错误",
                        http_status=resp.status_code,
                        log_id=log_id,
                    ),
                )

            event = data.get("data", {}).get("event", {})
            event_id = event.get("event_id") or event.get("id") or ""
            return FeishuCreateResult(success=True, event_id=event_id, calendar_id=calendar_id, raw_response=data)

        except requests.RequestException as exc:
            return FeishuCreateResult(success=False, error_message=f"飞书接口请求失败: {exc}")
        except FeishuCalendarError as exc:
            return FeishuCreateResult(success=False, error_message=str(exc))

    def _build_event_payload(self, schedule: dict) -> dict:
        _validate_schedule(schedule)

        title = schedule["title"]
        description = self._compose_description(schedule)
        start_time = self._to_feishu_time(schedule["start_time"], schedule.get("timezone", DEFAULT_TIMEZONE))
        end_time = self._to_feishu_time(schedule["end_time"], schedule.get("timezone", DEFAULT_TIMEZONE))

        return {
            "summary": title,
            "description": description,
            "start_time": start_time,
            "end_time": end_time,
        }

    def _compose_description(self, schedule: dict) -> str:
        lines = []
        if schedule.get("description"):
            lines.append(str(schedule["description"]).strip())
        if schedule.get("location"):
            lines.append(f"地点：{schedule['location']}")
        attendees = schedule.get("attendees") or []
        if attendees:
            lines.append("参会人员：" + "、".join(attendees))
        return "\n".join(lines).strip()

    def _to_feishu_time(self, dt_str: str, tz_name: str) -> dict:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        tzinfo = _timezone_from_name(tz_name)
        timestamp = int(dt.replace(tzinfo=tzinfo).timestamp())
        return {
            "timestamp": str(timestamp),
            "timezone": tz_name,
        }


def _timezone_from_name(tz_name: str):
    if tz_name == "Asia/Shanghai":
        return timezone(timedelta(hours=8))
    return timezone.utc


def _validate_schedule(schedule: dict[str, Any]):
    required_fields = ("title", "start_time", "end_time")
    for field in required_fields:
        value = schedule.get(field)
        if value is None or str(value).strip() == "":
            raise FeishuCalendarError(f"创建日程缺少必填字段: {field}")


def _safe_json(resp: requests.Response) -> dict[str, Any] | None:
    try:
        return resp.json()
    except ValueError:
        return None


def _now_ts() -> int:
    return int(time.time())


def _extract_feishu_message(data: dict[str, Any]) -> str:
    return str(
        data.get("error_description")
        or data.get("msg")
        or data.get("message")
        or data.get("error")
        or "未知错误"
    )


def _extract_token_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    if isinstance(nested, dict) and (nested.get("access_token") or nested.get("refresh_token")):
        return nested
    return data


def _expires_at_from_payload(payload: dict[str, Any], now_ts: int, candidate_keys: tuple[str, ...]) -> int | None:
    for key in candidate_keys:
        raw_value = payload.get(key)
        if raw_value in (None, ""):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if "expires_at" in key:
            return value
        if value > 0:
            return now_ts + value
    return None


def _response_indicates_auth_failure(resp: requests.Response) -> bool:
    if resp.status_code in {401, 403}:
        return True

    data = _safe_json(resp)
    if not isinstance(data, dict) or data.get("code") == 0:
        return False

    message = _extract_feishu_message(data).lower()
    code = data.get("code")
    if code in {99991661, 99991663, 99991668, 99991671}:
        return True

    auth_keywords = (
        "token",
        "unauthorized",
        "authorization",
        "access token",
        "user_access_token",
        "invalid user token",
        "expired",
    )
    return any(keyword in message for keyword in auth_keywords)


def _extract_log_id(resp: requests.Response, data: dict[str, Any] | None = None) -> str:
    if data and isinstance(data, dict):
        if data.get("log_id"):
            return str(data.get("log_id"))
    return str(resp.headers.get("X-Tt-Logid", "")).strip()


def _format_error_message(
    prefix: str,
    feishu_code: Any = None,
    feishu_msg: str = "",
    http_status: int | None = None,
    log_id: str = "",
    details: str = "",
) -> str:
    segments = [prefix]
    if feishu_code is not None:
        segments.append(f"code={feishu_code}")
    if feishu_msg:
        segments.append(f"msg={feishu_msg}")
    if http_status is not None:
        segments.append(f"http={http_status}")
    if log_id:
        segments.append(f"log_id={log_id}")
    if details:
        segments.append(details)
    return "; ".join(segments)


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    placeholders = {
        "your_feishu_app_id",
        "your_feishu_app_secret",
        "your_feishu_calendar_id",
        "your_app_id",
        "your_app_secret",
        "your_calendar_id",
        "replace_me",
        "todo",
        "example",
        "xxxx",
        "xxx",
    }
    return normalized in placeholders or normalized.startswith("your_")


def _suggest_fix_for_feishu_error(feishu_code: Any, feishu_msg: str) -> str:
    msg = str(feishu_msg).lower()
    if feishu_code == 10014 or "app secret invalid" in msg or "invalid app credential" in msg:
        return "请检查 .env 中的 FEISHU_APP_ID / FEISHU_APP_SECRET 是否填写了飞书应用后台的真实值，而不是模板占位符"
    if feishu_code in {20002, 20003, 20004, 20024, 20065, 20071}:
        return "请检查 FEISHU_AUTH_CODE、FEISHU_REDIRECT_URI、FEISHU_CODE_VERIFIER 是否与授权流程一致；授权码仅可使用一次且 5 分钟内有效"
    if "refresh token" in msg or "invalid refresh token" in msg:
        return "当前 refresh_token 可能已失效，请重新走一次飞书 OAuth 授权流程，获取新的 FEISHU_AUTH_CODE / refresh_token"
    return ""


