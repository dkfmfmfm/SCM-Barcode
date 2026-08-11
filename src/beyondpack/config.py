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
class SharePointSettings:
    tenant_id: str = ""
    client_id: str = ""
    site_id: str = ""
    list_id: str = ""
    authority_host: str = "https://login.microsoftonline.com"
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    scopes: list[str] = field(default_factory=lambda: ["Sites.Read.All"])


@dataclass(slots=True)
class AppConfig:
    source_type: str = "json"
    source_json_path: str = ""
    data_dir: str = ""
    operator_name: str = ""
    cache_max_age_hours: int = 72
    large_drop_threshold: float = 0.20
    scanner_terminator: str = "enter"
    weight_max_kg: float = 1000.0
    dimension_max_cm: float = 500.0
    sharepoint: SharePointSettings = field(default_factory=SharePointSettings)

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else default_app_dir() / "data"


def _from_dict(raw: dict[str, Any]) -> AppConfig:
    sharepoint = SharePointSettings(**raw.get("sharepoint", {}))
    values = {k: v for k, v in raw.items() if k != "sharepoint"}
    try:
        config = AppConfig(**values, sharepoint=sharepoint)
    except TypeError as exc:
        raise ConfigurationError(f"config.json 필드가 올바르지 않습니다: {exc}") from exc
    if config.source_type not in {"json", "sharepoint"}:
        raise ConfigurationError("source_type은 json 또는 sharepoint여야 합니다.")
    if not 0 <= config.large_drop_threshold < 1:
        raise ConfigurationError("large_drop_threshold는 0 이상 1 미만이어야 합니다.")
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
    return _from_dict(raw), config_path


def _config_dict(config: AppConfig) -> dict[str, Any]:
    return asdict(config)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
