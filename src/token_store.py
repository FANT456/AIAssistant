"""Local secure storage for Feishu OAuth tokens."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class TokenStoreError(RuntimeError):
    """Raised when the local token store cannot be read or written."""


@dataclass
class StoredFeishuToken:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int | None = None
    refresh_expires_at: int | None = None
    obtained_at: int | None = None
    token_type: str = "Bearer"
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "obtained_at": self.obtained_at,
            "token_type": self.token_type,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "StoredFeishuToken":
        return cls(
            access_token=str(payload.get("access_token") or "").strip(),
            refresh_token=str(payload.get("refresh_token") or "").strip(),
            expires_at=_to_int(payload.get("expires_at")),
            refresh_expires_at=_to_int(payload.get("refresh_expires_at")),
            obtained_at=_to_int(payload.get("obtained_at")),
            token_type=str(payload.get("token_type") or "Bearer").strip() or "Bearer",
            version=_to_int(payload.get("version")) or 1,
        )


class FeishuTokenStore:
    """Persist Feishu OAuth tokens locally, using DPAPI on Windows."""

    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path).expanduser()
        self.lock_path = self.storage_path.with_suffix(self.storage_path.suffix + ".lock")

    def load_token(self) -> StoredFeishuToken | None:
        if not self.storage_path.exists():
            return None

        try:
            raw_text = self.storage_path.read_text(encoding="utf-8")
            envelope = json.loads(raw_text)
            if not isinstance(envelope, dict):
                raise TokenStoreError("token 存储文件格式无效。")

            cipher = str(envelope.get("cipher") or "plain").strip().lower()
            payload_b64 = str(envelope.get("payload") or "").strip()
            if not payload_b64:
                raise TokenStoreError("token 存储文件缺少 payload。")

            payload_bytes = base64.b64decode(payload_b64.encode("ascii"))
            if cipher == "dpapi":
                payload_bytes = _unprotect_bytes(payload_bytes)
            elif cipher != "plain":
                raise TokenStoreError(f"不支持的 token 存储加密方式: {cipher}")

            payload = json.loads(payload_bytes.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TokenStoreError("token 数据内容无效。")

            token = StoredFeishuToken.from_dict(payload)
            if not token.access_token and not token.refresh_token:
                return None
            return token
        except TokenStoreError:
            raise
        except Exception as exc:  # pragma: no cover - defensive path
            raise TokenStoreError(f"读取本地 token 存储失败: {exc}") from exc

    def save_token(self, token: StoredFeishuToken):
        if not token.access_token and not token.refresh_token:
            raise TokenStoreError("拒绝保存空 token。")

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload_bytes = json.dumps(token.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")

        cipher = "plain"
        if sys.platform == "win32":
            payload_bytes = _protect_bytes(payload_bytes)
            cipher = "dpapi"

        envelope = {
            "version": 1,
            "cipher": cipher,
            "payload": base64.b64encode(payload_bytes).decode("ascii"),
        }

        temp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.storage_path)

        if sys.platform != "win32":
            os.chmod(self.storage_path, stat.S_IRUSR | stat.S_IWUSR)

    def clear(self):
        for path in (self.storage_path, self.lock_path, self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")):
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    @contextmanager
    def file_lock(self, timeout_seconds: float = 10.0, poll_interval: float = 0.1) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        handle: int | None = None

        while handle is None:
            try:
                handle = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(handle, str(os.getpid()).encode("ascii", errors="ignore"))
            except FileExistsError:
                if self._lock_is_stale(stale_after_seconds=max(60.0, timeout_seconds * 2)):
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TokenStoreError(f"等待 token 存储锁超时: {self.lock_path}")
                time.sleep(poll_interval)

        try:
            yield
        finally:
            if handle is not None:
                os.close(handle)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _lock_is_stale(self, stale_after_seconds: float) -> bool:
        try:
            return (time.time() - self.lock_path.stat().st_mtime) > stale_after_seconds
        except FileNotFoundError:
            return False


if sys.platform == "win32":  # pragma: no branch
    import ctypes
    from ctypes import wintypes

    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL


    def _bytes_to_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data)
        blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer


    def _protect_bytes(data: bytes) -> bytes:
        in_blob, buffer = _bytes_to_blob(data)
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "AIAssistant Feishu Token",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise TokenStoreError(f"Windows DPAPI 加密失败: {ctypes.WinError()}")

        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))
            _ = buffer


    def _unprotect_bytes(data: bytes) -> bytes:
        in_blob, buffer = _bytes_to_blob(data)
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise TokenStoreError(f"Windows DPAPI 解密失败: {ctypes.WinError()}")

        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))
            _ = buffer

else:

    def _protect_bytes(data: bytes) -> bytes:
        return data


    def _unprotect_bytes(data: bytes) -> bytes:
        return data


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


