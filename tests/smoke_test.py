"""AIAssistant 最小 smoke test。"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys
from importlib import import_module

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ChatDatabase = import_module("database").ChatDatabase
FeishuCalendarClient = import_module("feishu_client").FeishuCalendarClient
normalize_schedule_json = import_module("json_utils").normalize_schedule_json


def run():
    raw_output = """```json
    {
      "title": "第一季度安全生产会议",
      "start_time": "2026-03-23 13:30",
      "end_time": null,
      "timezone": "Asia/Shanghai",
      "location": "总部3号会议室",
      "attendees": ["部门负责人", "质量专员"],
      "description": "第一季度安全生产会议"
    }
    ```"""

    normalized = normalize_schedule_json(raw_output, source_text="测试文本")
    assert normalized["title"] == "第一季度安全生产会议"
    assert normalized["start_time"] == "2026-03-23 13:30"
    assert normalized["end_time"] == "2026-03-23 14:30"
    assert normalized["attendees"] == ["部门负责人", "质量专员"]

    client = FeishuCalendarClient(app_id="demo", app_secret="demo", calendar_id="calendar_demo")
    payload = client._build_event_payload(normalized)
    assert payload["summary"] == "第一季度安全生产会议"
    assert payload["start_time"]["timezone"] == "Asia/Shanghai"
    assert payload["end_time"]["timestamp"] > payload["start_time"]["timestamp"]

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "chat_history.db")
        db = ChatDatabase(db_path=db_path)
        db.save_message("user", "测试输入")
        db.save_schedule_record(
            input_type="text",
            source_name="",
            source_text="测试输入",
            extracted_json=normalized,
            status="success",
            feishu_event_id="event_demo",
        )
        assert db.get_message_count() == 1
        assert db.get_schedule_count() == 1
        record = db.get_recent_schedule_records(limit=1)[0]
        assert record["status"] == "success"
        assert record["feishu_event_id"] == "event_demo"

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    run()


