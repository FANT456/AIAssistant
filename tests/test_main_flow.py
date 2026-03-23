"""Main flow integration-style tests using natural-language input."""

from __future__ import annotations

import json
import tempfile
from importlib import import_module
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

main_module = import_module("main")
database_module = import_module("database")
feishu_module = import_module("feishu_client")

process_schedule_text = main_module.process_schedule_text
ChatDatabase = database_module.ChatDatabase
FeishuCreateResult = feishu_module.FeishuCreateResult


class DummyLLM:
    def __init__(self, raw_output: str, failed: bool = False, error_message: str = ""):
        self._raw_output = raw_output
        self.last_request_failed = failed
        self.last_error_message = error_message

    def extract_schedule_json(self, source_text: str) -> str:
        return self._raw_output


class DummyFeishu:
    def __init__(self, result: FeishuCreateResult):
        self._result = result
        self.received_schedule = None

    def create_event(self, schedule: dict) -> FeishuCreateResult:
        self.received_schedule = schedule
        return self._result


class MainFlowTests(unittest.TestCase):
    def test_process_schedule_text_success_from_natural_language(self):
        user_input = (
            "公司定于2026年3月23日下午13:30召开第一季度安全生产会议，"
            "地点在总部3号会议室，参会人员包括部门负责人和质量专员。"
        )
        llm_output = """```json
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

        llm = DummyLLM(raw_output=llm_output)
        feishu = DummyFeishu(FeishuCreateResult(success=True, event_id="evt_test_001"))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = ChatDatabase(db_path=str(Path(tmpdir) / "main_flow_success.db"))

            process_schedule_text(
                source_text=user_input,
                input_type="text",
                source_name="",
                db=db,
                llm=llm,
                feishu=feishu,
            )

            self.assertEqual(db.get_message_count(), 2)
            self.assertEqual(db.get_schedule_count(), 1)

            messages = db.get_all_messages()
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[0]["content"], user_input)
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertIn("第一季度安全生产会议", messages[1]["content"])

            records = db.get_recent_schedule_records(limit=1)
            self.assertEqual(records[0]["status"], "success")
            self.assertEqual(records[0]["feishu_event_id"], "evt_test_001")

            extracted = json.loads(records[0]["extracted_json"])
            self.assertEqual(extracted["title"], "第一季度安全生产会议")
            self.assertEqual(extracted["start_time"], "2026-03-23 13:30")
            self.assertEqual(extracted["end_time"], "2026-03-23 14:30")
            self.assertEqual(extracted["location"], "总部3号会议室")
            self.assertEqual(extracted["attendees"], ["部门负责人", "质量专员"])

            self.assertIsNotNone(feishu.received_schedule)
            self.assertEqual(feishu.received_schedule["end_time"], "2026-03-23 14:30")

    def test_process_schedule_text_feishu_failure_still_records_result(self):
        user_input = "明天下午3点在A会议室开项目例会，参会人员包括产品和开发。"
        llm_output = """{
          "title": "项目例会",
          "start_time": "2026-03-24 15:00",
          "end_time": "2026-03-24 16:00",
          "timezone": "Asia/Shanghai",
          "location": "A会议室",
          "attendees": ["产品", "开发"],
          "description": "项目例会"
        }"""

        llm = DummyLLM(raw_output=llm_output)
        feishu = DummyFeishu(FeishuCreateResult(success=False, error_message="calendar api rejected request"))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = ChatDatabase(db_path=str(Path(tmpdir) / "main_flow_failure.db"))

            process_schedule_text(
                source_text=user_input,
                input_type="text",
                source_name="",
                db=db,
                llm=llm,
                feishu=feishu,
            )

            self.assertEqual(db.get_message_count(), 2)
            self.assertEqual(db.get_schedule_count(), 1)

            record = db.get_recent_schedule_records(limit=1)[0]
            self.assertEqual(record["status"], "feishu_failed")
            self.assertEqual(record["error_message"], "calendar api rejected request")
            self.assertEqual(record["feishu_event_id"], "")


if __name__ == "__main__":
    unittest.main()

