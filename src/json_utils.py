"""
JSON 处理工具：负责从模型输出中提取、校验并标准化日程 JSON。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from config import DEFAULT_EVENT_DURATION_MINUTES, DEFAULT_TIMEZONE

SUPPORTED_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
)


class ScheduleJsonError(ValueError):
    """日程 JSON 解析/校验错误。"""


def extract_json_block(raw_text: str) -> dict[str, Any]:
    """从模型输出文本中提取第一个 JSON 对象。"""
    if not raw_text or not raw_text.strip():
        raise ScheduleJsonError("模型未返回任何内容。")

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = _strip_code_fences(cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = cleaned.find("{", start + 1)

    raise ScheduleJsonError("未能从模型输出中提取合法 JSON 对象。")


def normalize_schedule_json(raw_text: str, source_text: str = "") -> dict[str, Any]:
    """将模型输出标准化为可入历的日程对象。"""
    data = extract_json_block(raw_text)

    title = str(data.get("title") or "未命名日程").strip() or "未命名日程"
    timezone = str(data.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    location = str(data.get("location") or "").strip()
    description = str(data.get("description") or title).strip() or title

    attendees = data.get("attendees") or []
    if not isinstance(attendees, list):
        raise ScheduleJsonError("attendees 字段必须是数组。")
    attendees = [str(item).strip() for item in attendees if str(item).strip()]

    start_time_raw = data.get("start_time")
    if not start_time_raw:
        raise ScheduleJsonError("缺少 start_time 字段。")

    start_dt = _parse_datetime(str(start_time_raw).strip())

    end_time_raw = data.get("end_time")
    if end_time_raw in (None, "", "null"):
        end_dt = start_dt + timedelta(minutes=DEFAULT_EVENT_DURATION_MINUTES)
    else:
        end_dt = _parse_datetime(str(end_time_raw).strip())

    if end_dt <= start_dt:
        raise ScheduleJsonError("end_time 必须晚于 start_time。")

    fallback = _extract_fallback_fields(source_text)
    if title == "未命名日程" and fallback["title"]:
        title = fallback["title"]
    if not location and fallback["location"]:
        location = fallback["location"]
    if not attendees and fallback["attendees"]:
        attendees = fallback["attendees"]
    if description == "未命名日程":
        description = fallback["description"] or title

    normalized = {
        "title": title,
        "start_time": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end_time": end_dt.strftime("%Y-%m-%d %H:%M"),
        "timezone": timezone,
        "location": location,
        "attendees": attendees,
        "description": description,
    }

    if source_text:
        normalized["source_text"] = source_text

    return normalized


def to_pretty_json(data: dict[str, Any]) -> str:
    """格式化 JSON 便于终端展示与落库。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def _strip_code_fences(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return text

    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_datetime(value: str) -> datetime:
    for fmt in SUPPORTED_DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ScheduleJsonError(f"无法识别的时间格式: {value}")


def _extract_fallback_fields(source_text: str) -> dict[str, Any]:
    text = source_text.strip()
    if not text:
        return {
            "title": "",
            "location": "",
            "attendees": [],
            "description": "",
        }

    title = _search_first_group(
        text,
        [
            r"召开(?P<value>[^。；;，,]{2,40}?会议)",
            r"举行(?P<value>[^。；;，,]{2,40}?会议)",
            r"参加(?P<value>[^。；;，,]{2,40}?会议)",
        ],
    )
    location = _search_first_group(
        text,
        [
            r"地点(?:在|为|是|：|:)?(?P<value>[^。；;，,]+)",
            r"地点设在(?P<value>[^。；;，,]+)",
        ],
    )
    attendees_raw = _search_first_group(
        text,
        [
            r"参会人员(?:包括|有|为|：|:)?(?P<value>[^。；;]+)",
            r"参会人员(?P<value>[^。；;]+)",
            r"参加人员(?:包括|有|为|：|:)?(?P<value>[^。；;]+)",
        ],
    )
    attendees = _split_attendees(attendees_raw)
    description = title or text[:120]

    return {
        "title": title,
        "location": location,
        "attendees": attendees,
        "description": description,
    }


def _search_first_group(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group("value").strip(" ，,。；;")
            if value:
                return value
    return ""


def _split_attendees(value: str) -> list[str]:
    if not value:
        return []

    cleaned = value.strip().strip("。；;")
    cleaned = re.sub(r"再次确认.*$", "", cleaned)
    parts = re.split(r"[、，,]|和|及|与", cleaned)
    attendees = []
    for part in parts:
        item = part.strip()
        if item:
            attendees.append(item)
    return attendees


