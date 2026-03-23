"""FeishuCalendarClient API contract tests with mocked HTTP responses."""

from __future__ import annotations

import unittest
import tempfile
import time
from importlib import import_module
from pathlib import Path
from unittest.mock import patch
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

feishu_module = import_module("feishu_client")
config_module = import_module("config")
token_store_module = import_module("token_store")

FeishuCalendarClient = feishu_module.FeishuCalendarClient
FeishuCalendarError = feishu_module.FeishuCalendarError
FeishuTokenStore = token_store_module.FeishuTokenStore
StoredFeishuToken = token_store_module.StoredFeishuToken


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FeishuClientTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.token_store = FeishuTokenStore(Path(self.tempdir.name) / "feishu_tokens.dat")
        self.client = FeishuCalendarClient(
            app_id="app_demo",
            app_secret="secret_demo",
            user_access_token="user_token_demo",
            calendar_id="calendar_demo",
            token_store=self.token_store,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def build_client(self, **overrides):
        defaults = {
            "app_id": "app_demo",
            "app_secret": "secret_demo",
            "user_access_token": "",
            "refresh_token": "",
            "auth_code": "",
            "calendar_id": "calendar_demo",
            "token_store": self.token_store,
        }
        defaults.update(overrides)
        return FeishuCalendarClient(**defaults)

    @patch("feishu_client.requests.post")
    def test_get_user_access_token_prefers_direct_env_token(self, mock_post):
        token = self.client.get_user_access_token()

        self.assertEqual(token, "user_token_demo")
        mock_post.assert_not_called()

    @patch("feishu_client.requests.post")
    def test_get_user_access_token_success_with_auth_code(self, mock_post):
        client = self.build_client(
            auth_code="auth_code_demo",
            redirect_uri="https://example.com/callback",
            code_verifier="x" * 43,
        )
        mock_post.return_value = FakeResponse(payload={"code": 0, "access_token": "user_token_123"})

        token = client.get_user_access_token()

        self.assertEqual(token, "user_token_123")
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], config_module.FEISHU_API_USER_TOKEN)
        self.assertEqual(kwargs["json"]["grant_type"], "authorization_code")
        self.assertEqual(kwargs["json"]["client_id"], "app_demo")
        self.assertEqual(kwargs["json"]["client_secret"], "secret_demo")
        self.assertEqual(kwargs["json"]["code"], "auth_code_demo")
        self.assertEqual(kwargs["json"]["redirect_uri"], "https://example.com/callback")
        self.assertEqual(kwargs["json"]["code_verifier"], "x" * 43)

    @patch("feishu_client.requests.post")
    def test_get_user_access_token_business_error_with_log_id(self, mock_post):
        client = self.build_client(
            auth_code="expired_or_invalid_code",
        )
        mock_post.return_value = FakeResponse(
            payload={"code": 20003, "error_description": "authorization code not found"},
            headers={"X-Tt-Logid": "log_user_token_001"},
        )

        with self.assertRaises(FeishuCalendarError) as ctx:
            client.get_user_access_token()

        message = str(ctx.exception)
        self.assertIn("code=20003", message)
        self.assertIn("authorization code not found", message)
        self.assertIn("log_id=log_user_token_001", message)

    def test_validate_configuration_rejects_placeholder_values(self):
        client = FeishuCalendarClient(
            app_id="your_feishu_app_id",
            app_secret="your_feishu_app_secret",
            auth_code="your_auth_code",
            calendar_id="your_feishu_calendar_id",
            token_store=self.token_store,
        )

        self.assertFalse(client.has_valid_configuration())
        issues = client.get_invalid_config_issues()
        self.assertTrue(any("FEISHU_APP_ID" in issue for issue in issues))
        self.assertTrue(any("FEISHU_APP_SECRET" in issue for issue in issues))
        self.assertTrue(any("FEISHU_AUTH_CODE" in issue for issue in issues))
        self.assertTrue(any("FEISHU_CALENDAR_ID" in issue for issue in issues))

    def test_validate_configuration_rejects_template_secret_suffix(self):
        client = FeishuCalendarClient(
            app_id="cli_demo_app",
            app_secret="secret_value.env.example",
            auth_code="auth_code_demo",
            calendar_id="calendar_demo_123456",
            token_store=self.token_store,
        )

        self.assertFalse(client.has_valid_configuration())
        with self.assertRaises(FeishuCalendarError) as ctx:
            client.validate_configuration()

        self.assertIn("FEISHU_APP_SECRET", str(ctx.exception))

    @patch("feishu_client.requests.get")
    @patch("feishu_client.requests.post")
    def test_resolve_calendar_id_prefers_primary_calendar_from_list(self, mock_post, mock_get):
        client = self.build_client(
            auth_code="auth_code_demo",
            calendar_id="",
        )
        mock_post.return_value = FakeResponse(payload={"code": 0, "access_token": "token_ok"})
        mock_get.return_value = FakeResponse(
            payload={
                "code": 0,
                "data": {
                    "has_more": False,
                    "page_token": "",
                    "calendar_list": [
                        {"calendar_id": "cal_secondary", "summary": "次日历", "role": "writer", "type": "shared"},
                        {"calendar_id": "cal_primary_api", "summary": "主日历", "type": "primary", "role": "owner"},
                    ],
                },
            }
        )

        resolved = client.resolve_calendar_id()

        self.assertEqual(resolved, "cal_primary_api")
        self.assertEqual(client.resolved_calendar_id, "cal_primary_api")
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["page_size"], 500)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token_ok")

    @patch("feishu_client.requests.get")
    @patch("feishu_client.requests.post")
    def test_list_calendars_handles_pagination(self, mock_post, mock_get):
        client = self.build_client(
            auth_code="auth_code_demo",
            calendar_id="",
        )
        mock_post.return_value = FakeResponse(payload={"code": 0, "access_token": "token_ok"})
        mock_get.side_effect = [
            FakeResponse(
                payload={
                    "code": 0,
                    "data": {
                        "has_more": True,
                        "page_token": "page_2",
                        "calendar_list": [
                            {"calendar_id": "cal_first", "summary": "第一页日历", "role": "writer", "type": "shared"},
                        ],
                    },
                }
            ),
            FakeResponse(
                payload={
                    "code": 0,
                    "data": {
                        "has_more": False,
                        "page_token": "",
                        "calendar_list": [
                            {"calendar_id": "cal_primary", "summary": "主日历", "type": "primary", "role": "owner"},
                        ]
                    },
                }
            ),
        ]

        calendars = client.list_calendars()

        self.assertEqual(len(calendars), 2)
        self.assertEqual(calendars[0]["calendar_id"], "cal_first")
        self.assertEqual(calendars[1]["calendar_id"], "cal_primary")
        self.assertEqual(mock_get.call_count, 2)
        first_call_kwargs = mock_get.call_args_list[0][1]
        second_call_kwargs = mock_get.call_args_list[1][1]
        self.assertEqual(first_call_kwargs["params"], {"page_size": 500})
        self.assertEqual(second_call_kwargs["params"], {"page_size": 500, "page_token": "page_2"})

    @patch("feishu_client.requests.get")
    @patch("feishu_client.requests.post")
    def test_create_event_without_explicit_calendar_id_uses_discovered_calendar(self, mock_post, mock_get):
        client = self.build_client(
            auth_code="auth_code_demo",
            calendar_id="",
        )

        token_resp = FakeResponse(payload={"code": 0, "access_token": "token_ok"})
        create_resp = FakeResponse(
            payload={
                "code": 0,
                "msg": "success",
                "data": {"event": {"event_id": "evt_auto_001"}},
            }
        )
        mock_post.side_effect = [token_resp, create_resp]
        mock_get.return_value = FakeResponse(
            payload={
                "code": 0,
                "data": {
                    "has_more": False,
                    "page_token": "",
                    "calendar_list": [
                        {"calendar_id": "cal_auto_primary", "summary": "主日历", "type": "primary", "role": "owner"}
                    ],
                },
            }
        )

        schedule = {
            "title": "项目例会",
            "start_time": "2026-03-24 10:00",
            "end_time": "2026-03-24 11:00",
        }

        result = client.create_event(schedule)

        self.assertTrue(result.success)
        self.assertEqual(result.event_id, "evt_auto_001")
        self.assertEqual(result.calendar_id, "cal_auto_primary")
        self.assertIn("/cal_auto_primary/events", mock_post.call_args_list[1][0][0])
        mock_get.assert_called_once()

    @patch("feishu_client.requests.post")
    def test_create_event_success(self, mock_post):
        create_resp = FakeResponse(
            payload={
                "code": 0,
                "msg": "success",
                "data": {"event": {"event_id": "evt_001"}},
            }
        )
        mock_post.return_value = create_resp

        schedule = {
            "title": "项目例会",
            "start_time": "2026-03-24 10:00",
            "end_time": "2026-03-24 11:00",
            "timezone": "Asia/Shanghai",
            "location": "A会议室",
            "attendees": ["张三"],
            "description": "周会",
        }

        result = self.client.create_event(schedule)

        self.assertTrue(result.success)
        self.assertEqual(result.event_id, "evt_001")
        self.assertEqual(mock_post.call_count, 1)

        _, create_kwargs = mock_post.call_args_list[0]
        self.assertIn("Authorization", create_kwargs["headers"])
        self.assertEqual(create_kwargs["headers"]["Authorization"], "Bearer user_token_demo")
        self.assertIn("/calendar_demo/events", mock_post.call_args_list[0][0][0])
        self.assertEqual(create_kwargs["json"]["summary"], "项目例会")
        self.assertEqual(create_kwargs["json"]["start_time"]["timezone"], "Asia/Shanghai")

    @patch("feishu_client.requests.post")
    def test_refreshes_token_before_expiry_and_persists_result(self, mock_post):
        now_ts = int(time.time())
        self.token_store.save_token(
            StoredFeishuToken(
                access_token="old_access",
                refresh_token="refresh_old",
                expires_at=now_ts + 30,
                refresh_expires_at=now_ts + 3600,
                obtained_at=now_ts,
            )
        )
        client = self.build_client(refresh_skew_seconds=300)
        mock_post.return_value = FakeResponse(
            payload={
                "code": 0,
                "access_token": "new_access",
                "refresh_token": "refresh_new",
                "expires_in": 7200,
                "refresh_expires_in": 86400,
            }
        )

        token = client.get_user_access_token()

        self.assertEqual(token, "new_access")
        stored = self.token_store.load_token()
        self.assertIsNotNone(stored)
        self.assertEqual(stored.access_token, "new_access")
        self.assertEqual(stored.refresh_token, "refresh_new")
        self.assertGreater(stored.expires_at, now_ts)
        self.assertEqual(mock_post.call_args[1]["json"]["grant_type"], "refresh_token")
        self.assertEqual(mock_post.call_args[1]["json"]["refresh_token"], "refresh_old")

    @patch("feishu_client.requests.post")
    def test_new_client_instance_uses_persisted_token_without_extra_http(self, mock_post):
        now_ts = int(time.time())
        self.token_store.save_token(
            StoredFeishuToken(
                access_token="persisted_access",
                refresh_token="persisted_refresh",
                expires_at=now_ts + 7200,
                refresh_expires_at=now_ts + 86400,
                obtained_at=now_ts,
            )
        )
        client = self.build_client(user_access_token="", refresh_token="", auth_code="")

        token = client.get_user_access_token()

        self.assertEqual(token, "persisted_access")
        mock_post.assert_not_called()

    @patch("feishu_client.requests.post")
    def test_create_event_retries_once_after_auth_failure(self, mock_post):
        client = self.build_client(user_access_token="", refresh_token="refresh_seed")
        mock_post.side_effect = [
            FakeResponse(payload={"code": 0, "access_token": "token_first", "refresh_token": "refresh_1", "expires_in": 3600}),
            FakeResponse(payload={"code": 99991663, "msg": "invalid access token"}),
            FakeResponse(payload={"code": 0, "access_token": "token_second", "refresh_token": "refresh_2", "expires_in": 3600}),
            FakeResponse(payload={"code": 0, "msg": "success", "data": {"event": {"event_id": "evt_retry_001"}}}),
        ]

        schedule = {
            "title": "项目例会",
            "start_time": "2026-03-24 10:00",
            "end_time": "2026-03-24 11:00",
        }

        result = client.create_event(schedule)

        self.assertTrue(result.success)
        self.assertEqual(result.event_id, "evt_retry_001")
        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(mock_post.call_args_list[1][1]["headers"]["Authorization"], "Bearer token_first")
        self.assertEqual(mock_post.call_args_list[3][1]["headers"]["Authorization"], "Bearer token_second")

    @patch("feishu_client.requests.post")
    def test_create_event_business_error(self, mock_post):
        create_resp = FakeResponse(
            payload={"code": 2003201, "msg": "calendar not found"},
            headers={"X-Tt-Logid": "log_create_001"},
        )
        mock_post.return_value = create_resp

        schedule = {
            "title": "项目例会",
            "start_time": "2026-03-24 10:00",
            "end_time": "2026-03-24 11:00",
        }

        result = self.client.create_event(schedule)

        self.assertFalse(result.success)
        self.assertIn("code=2003201", result.error_message)
        self.assertIn("msg=calendar not found", result.error_message)
        self.assertIn("log_id=log_create_001", result.error_message)

    @patch("feishu_client.requests.post")
    def test_create_event_http_exception(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("connection failed")

        schedule = {
            "title": "项目例会",
            "start_time": "2026-03-24 10:00",
            "end_time": "2026-03-24 11:00",
        }

        result = self.client.create_event(schedule)

        self.assertFalse(result.success)
        self.assertIn("connection failed", result.error_message)
        self.assertTrue(
            "获取 user_access_token HTTP 失败" in result.error_message
            or "飞书接口请求失败" in result.error_message
        )


if __name__ == "__main__":
    unittest.main()


