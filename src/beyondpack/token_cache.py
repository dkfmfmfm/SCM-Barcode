from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _protect_windows(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "BeyondPack", None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer


def _unprotect_windows(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer


class SecureTokenStore:
    """Persist MSAL cache with Windows DPAPI; development platforms use mode 0600."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str:
        if not self.path.exists():
            return ""
        try:
            encoded = self.path.read_bytes()
            payload = base64.b64decode(encoded)
            if os.name == "nt":
                payload = _unprotect_windows(payload)
            return payload.decode("utf-8")
        except Exception:
            return ""

    def save(self, serialized: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = serialized.encode("utf-8")
        if os.name == "nt":
            payload = _protect_windows(payload)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(base64.b64encode(payload))
        if os.name != "nt":
            os.chmod(temp, 0o600)
        os.replace(temp, self.path)

