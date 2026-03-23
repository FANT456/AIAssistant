"""Tests for local Feishu token persistence."""

from __future__ import annotations

import sys
import tempfile
from importlib import import_module
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

token_store_module = import_module("token_store")

FeishuTokenStore = token_store_module.FeishuTokenStore
StoredFeishuToken = token_store_module.StoredFeishuToken


class TokenStoreTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "feishu_tokens.dat"
            store = FeishuTokenStore(store_path)
            token = StoredFeishuToken(
                access_token="access_demo_123",
                refresh_token="refresh_demo_456",
                expires_at=1893456000,
                refresh_expires_at=1893542400,
                obtained_at=1893452400,
            )

            store.save_token(token)
            loaded = store.load_token()

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.access_token, token.access_token)
            self.assertEqual(loaded.refresh_token, token.refresh_token)
            self.assertEqual(loaded.expires_at, token.expires_at)
            self.assertEqual(loaded.refresh_expires_at, token.refresh_expires_at)
            self.assertTrue(store_path.exists())

            raw_text = store_path.read_text(encoding="utf-8")
            if sys.platform == "win32":
                self.assertNotIn("access_demo_123", raw_text)
                self.assertNotIn("refresh_demo_456", raw_text)


if __name__ == "__main__":
    unittest.main()

