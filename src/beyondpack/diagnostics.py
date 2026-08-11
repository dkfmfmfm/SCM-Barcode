from __future__ import annotations

import json
import platform
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from . import __version__
from .cache import ProductCacheRepository


def create_diagnostic_bundle(data_dir: Path, output_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"diagnostics-{stamp}.zip"
    cache = ProductCacheRepository(data_dir)
    info = cache.info()
    summary = {
        "app_version": __version__,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "product_cache": {
            "count": info.product_count,
            "data_version": info.data_version,
            "schema_version": info.schema_version,
            "synced_at": info.synced_at,
        },
        "files": {
            path.name: {"size": path.stat().st_size, "modified": path.stat().st_mtime}
            for path in data_dir.glob("*")
            if path.is_file() and "token" not in path.name.casefold()
        },
    }
    db_check = {}
    for db_name in ("products.db", "packaging.db"):
        db_path = data_dir / db_name
        if db_path.exists():
            try:
                with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                    db_check[db_name] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.Error as exc:
                db_check[db_name] = f"ERROR: {exc}"
    summary["database_integrity"] = db_check
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        status_path = data_dir / "sync-status.json"
        if status_path.exists():
            archive.write(status_path, "sync-status.json")
    return output
