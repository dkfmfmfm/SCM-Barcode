from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


def default_app_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "BeyondPack"


@dataclass(slots=True)
class GoogleSheetsSettings:
    spreadsheet_url: str = ""
    gid: str = ""
    timeout_seconds: int = 10
    max_download_bytes: int = 20_000_000


@dataclass(slots=True)
class LabelSettings:
    """라벨 프린터와 용지 규격. 지정하지 않으면 인쇄할 때마다 프린터를 고른다."""

    printer_name: str = ""
    width_mm: float = 40.0
    height_mm: float = 25.0
    margin_mm: float = 1.5
    auto_print: bool = True

    # 드라이버가 보고하는 용지 크기의 반올림 오차 허용치(mm).
    PAGE_TOLERANCE_MM = 1.0

    def matches_page(self, width_mm: float, height_mm: float) -> bool:
        """프린터에 실제로 적용된 용지가 설정한 라벨 크기와 같은지 판정한다.

        Windows 드라이버에 해당 사용자 정의 용지가 없으면 크기 지정이 조용히
        실패하고 A4가 유지된다. 그 상태로 인쇄하면 라벨이 잘리고 빈 라벨이
        함께 배출되므로 인쇄 전에 이 검사로 걸러낸다.
        """
        return (
            abs(width_mm - self.width_mm) <= self.PAGE_TOLERANCE_MM
            and abs(height_mm - self.height_mm) <= self.PAGE_TOLERANCE_MM
        )


@dataclass(slots=True)
class BackupSettings:
    """포장 실적을 작업 PC 밖으로 복사하는 설정.

    `packaging.db`는 이 PC에만 있어 디스크가 고장나면 복구할 수 없다. 공유
    드라이브 등 다른 위치를 지정해 두면 주기적으로 사본을 남긴다.
    """

    directory: str = ""
    enabled: bool = True
    interval_minutes: int = 10
    keep_days: int = 90

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.directory.strip())


@dataclass(slots=True)
class AppConfig:
    source_type: str = "google_sheets"
    source_json_path: str = ""
    data_dir: str = ""
    operator_name: str = ""
    cache_max_age_hours: int = 72
    large_drop_threshold: float = 0.20
    scanner_terminator: str = "enter"
    weight_max_kg: float = 1000.0
    dimension_max_cm: float = 500.0
    google_sheets: GoogleSheetsSettings = field(default_factory=GoogleSheetsSettings)
    label: LabelSettings = field(default_factory=LabelSettings)
    backup: BackupSettings = field(default_factory=BackupSettings)

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else default_app_dir() / "data"


def _from_dict(raw: dict[str, Any]) -> AppConfig:
    try:
        google_sheets = GoogleSheetsSettings(**raw.get("google_sheets", {}))
        label = LabelSettings(**raw.get("label", {}))
        backup = BackupSettings(**raw.get("backup", {}))
    except TypeError as exc:
        raise ConfigurationError(f"config.json 필드가 올바르지 않습니다: {exc}") from exc
    values = {
        k: v
        for k, v in raw.items()
        if k not in {"sharepoint", "google_sheets", "label", "backup"}
    }
    # 2.1.x used SharePoint. 2.2 migrates that setting to the new primary
    # source without preserving credentials or opening a Microsoft login flow.
    if values.get("source_type") == "sharepoint":
        values["source_type"] = "google_sheets"
    try:
        config = AppConfig(
            **values, google_sheets=google_sheets, label=label, backup=backup
        )
    except TypeError as exc:
        raise ConfigurationError(f"config.json 필드가 올바르지 않습니다: {exc}") from exc
    if config.source_type not in {"json", "google_sheets"}:
        raise ConfigurationError("source_type은 google_sheets 또는 json이어야 합니다.")
    if not 0 <= config.large_drop_threshold < 1:
        raise ConfigurationError("large_drop_threshold는 0 이상 1 미만이어야 합니다.")
    if config.label.width_mm <= 0 or config.label.height_mm <= 0:
        raise ConfigurationError("라벨 가로·세로(mm)는 0보다 커야 합니다.")
    if config.label.margin_mm < 0:
        raise ConfigurationError("라벨 여백(mm)은 0 이상이어야 합니다.")
    if config.backup.interval_minutes < 1:
        raise ConfigurationError("백업 주기(분)는 1 이상이어야 합니다.")
    if config.backup.keep_days < 1:
        raise ConfigurationError("백업 보관일수는 1 이상이어야 합니다.")
    return config


def load_config(path: Path | None = None) -> tuple[AppConfig, Path]:
    config_path = path or default_app_dir() / "config.json"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = AppConfig()
        _atomic_json_write(config_path, _config_dict(config))
        return config, config_path
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"설정 파일을 읽을 수 없습니다: {config_path}") from exc
    config = _from_dict(raw)
    if raw.get("source_type") == "sharepoint":
        save_config(config, config_path)
    return config, config_path


def _config_dict(config: AppConfig) -> dict[str, Any]:
    return asdict(config)


def save_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(path, _config_dict(config))


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
