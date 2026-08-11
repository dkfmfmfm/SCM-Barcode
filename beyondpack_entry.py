from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _log_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "BeyondPack" / "logs" / "startup.log"


def _write_startup_log(message: str) -> Path:
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:
            previous = path.with_name("startup.previous.log")
            previous.unlink(missing_ok=True)
            path.replace(previous)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {message}\n")
    except OSError:
        pass
    return path


def _show_fatal_error(message: str, log_path: Path) -> None:
    detail = (
        "BeyondPack을 시작하지 못했습니다.\n\n"
        f"{message}\n\n"
        f"오류 기록: {log_path}"
    )
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, "BeyondPack 시작 오류", 0x10)
            return
        except Exception:
            pass
    print(detail, file=sys.stderr)


def _unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    path = _write_startup_log("UNHANDLED ERROR\n" + details)
    _show_fatal_error(str(exc_value), path)


def run() -> int:
    _write_startup_log("START BeyondPack")
    sys.excepthook = _unhandled_exception
    try:
        from beyondpack.app import main

        exit_code = main()
    except BaseException as exc:
        details = traceback.format_exc()
        path = _write_startup_log("FATAL STARTUP ERROR\n" + details)
        _show_fatal_error(str(exc), path)
        return 1
    _write_startup_log(f"EXIT code={exit_code}")
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(run())
