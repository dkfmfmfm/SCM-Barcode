from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator

from .errors import (
    CountrySelectionRequiredError,
    DataValidationError,
    DuplicateProductKeyError,
    InactiveProductError,
    ProductNotFoundError,
)
from .models import Product, utc_now_iso
from .normalization import normalize_country_code, normalize_fnsku
from .sources.base import ProductBatch


EXPECTED_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class CacheInfo:
    product_count: int
    data_version: str
    schema_version: int
    synced_at: str


class ProductCacheRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        # products.db and products.previous.db are retained as read-compatible
        # legacy files. New snapshots use immutable names so a process, antivirus,
        # or backup agent holding the old file cannot block an update on Windows.
        self.db_path = data_dir / "products.db"
        self.previous_path = data_dir / "products.previous.db"
        self.manifest_path = data_dir / "products.current.json"
        self._io_lock = RLock()

    def _new_snapshot_path(self) -> Path:
        return self.data_dir / f"products.snapshot.{os.getpid()}.{uuid.uuid4().hex}.db"

    @property
    def active_db_path(self) -> Path:
        with self._io_lock:
            return self._active_db_path()

    def exists(self) -> bool:
        with self._io_lock:
            return self._active_db_path().exists()

    def replace_snapshot(self, batch: ProductBatch, drop_threshold: float = 0.20) -> CacheInfo:
        with self._io_lock:
            products = self._validate_batch(batch)
            previous_db = self._active_db_path()
            previous_count = (
                self._info_from_path(previous_db).product_count if previous_db.exists() else 0
            )
            if previous_count and len(products) < previous_count * (1 - drop_threshold):
                drop = 1 - (len(products) / previous_count)
                raise DataValidationError(
                    f"상품 수가 이전보다 {drop:.1%} 감소해 새 데이터 적용을 중단했습니다."
                )
            self.data_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = self._new_snapshot_path()
            synced_at = utc_now_iso()
            activated = False
            try:
                self._write_database(snapshot_path, products, batch, synced_at)
                self._integrity_check(snapshot_path, len(products))
                self._write_manifest(
                    current=snapshot_path,
                    previous=previous_db if previous_db.exists() else None,
                    synced_at=synced_at,
                )
                activated = True
            finally:
                if not activated:
                    try:
                        snapshot_path.unlink(missing_ok=True)
                    except OSError:
                        # A scanner can briefly keep a failed snapshot open. It is not
                        # referenced by the manifest, so leaving it cannot affect work.
                        pass
            self._cleanup_old_snapshots(snapshot_path, previous_db)
            return CacheInfo(len(products), batch.data_version, batch.schema_version, synced_at)

    def _write_manifest(
        self, current: Path, previous: Path | None, synced_at: str
    ) -> None:
        payload = {
            "manifest_version": 1,
            "current": current.name,
            "previous": previous.name if previous else "",
            "updated_at": synced_at,
        }
        temp_path = self.data_dir / (
            f"products.current.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_with_retry(temp_path, self.manifest_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _active_db_path(self) -> Path:
        manifest = self._load_manifest()
        for field in ("current", "previous"):
            candidate = self._manifest_db_path(manifest.get(field))
            if candidate is not None and candidate.exists():
                return candidate
        if self.db_path.exists():
            return self.db_path
        if self.previous_path.exists():
            return self.previous_path
        return self.db_path

    def _load_manifest(self) -> dict[str, object]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _manifest_db_path(self, value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        name = Path(value)
        if name.name != value or not value.startswith("products") or not value.endswith(".db"):
            return None
        return self.data_dir / name

    def _cleanup_old_snapshots(self, current: Path, previous: Path) -> None:
        keep = {current.name, previous.name}
        candidates = tuple(self.data_dir.glob("products.snapshot.*.db")) + tuple(
            self.data_dir.glob("products.*.new.db")
        )
        for path in candidates:
            if path.name in keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup is best-effort. A locked orphan is never selected because
                # only files named in products.current.json can become active.
                pass

    @staticmethod
    def _replace_with_retry(source: Path, target: Path, attempts: int = 10) -> None:
        for attempt in range(attempts):
            try:
                os.replace(source, target)
                return
            except OSError as exc:
                is_windows_lock = isinstance(exc, PermissionError) or getattr(
                    exc, "winerror", None
                ) in {32, 33}
                if not is_windows_lock or attempt == attempts - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def available_countries(self) -> tuple[tuple[str, str], ...]:
        with self._io_lock:
            db_path = self._active_db_path()
            if not db_path.exists():
                return ()
            with self._connect(db_path, read_only=True) as conn:
                rows = conn.execute(
                    """
                    SELECT country_code, MAX(country_name) AS country_name
                    FROM products
                    WHERE lower(status) = 'published'
                    GROUP BY country_code
                    ORDER BY country_code
                    """
                ).fetchall()
            return tuple((str(row["country_code"]), str(row["country_name"])) for row in rows)

    def lookup(self, fnsku: object, country_code: object) -> Product:
        key = normalize_fnsku(fnsku)
        country = normalize_country_code(country_code)
        if not key:
            raise ProductNotFoundError("FNSKU를 스캔하거나 입력하세요.")
        if not country:
            raise CountrySelectionRequiredError("먼저 작업 국가를 선택하세요.")
        with self._io_lock:
            db_path = self._active_db_path()
            if not db_path.exists():
                raise ProductNotFoundError("사용할 상품DB가 없습니다. 상품정보를 업데이트하세요.")
            with self._connect(db_path, read_only=True) as conn:
                row = conn.execute(
                    """
                    SELECT fnsku, item_code, sku, country_code, country_name,
                           product_name, product_name_en, amazon_account, status,
                           source_modified_at, data_version, schema_version
                    FROM products WHERE normalized_fnsku = ? AND country_code = ?
                    """,
                    (key, country),
                ).fetchone()
                available = conn.execute(
                    """
                    SELECT country_code FROM products
                    WHERE normalized_fnsku = ? AND lower(status) = 'published'
                    ORDER BY country_code
                    """,
                    (key,),
                ).fetchall()
        if row is None:
            if available:
                codes = ", ".join(str(item["country_code"]) for item in available)
                raise ProductNotFoundError(
                    f"{country} 국가에는 등록되지 않은 FNSKU입니다. 등록 국가: {codes}"
                )
            raise ProductNotFoundError(
                "등록되지 않은 FNSKU입니다. 상품정보를 업데이트하거나 관리자에게 문의하세요."
            )
        product = Product(*row)
        if product.status.casefold() != "published":
            raise InactiveProductError("사용중지된 상품입니다. 포장할 수 없습니다.")
        return product

    def info(self) -> CacheInfo:
        with self._io_lock:
            db_path = self._active_db_path()
            if not db_path.exists():
                return CacheInfo(0, "", 0, "")
            return self._info_from_path(db_path)

    def _info_from_path(self, path: Path) -> CacheInfo:
        with self._connect(path, read_only=True) as conn:
            values = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
            count = int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])
        return CacheInfo(
            count,
            values.get("data_version", ""),
            int(values.get("schema_version", 0)),
            values.get("synced_at", ""),
        )

    def cache_age_hours(self) -> float | None:
        synced = self.info().synced_at
        if not synced:
            return None
        moment = datetime.fromisoformat(synced.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - moment).total_seconds() / 3600

    @staticmethod
    def _validate_batch(batch: ProductBatch) -> tuple[Product, ...]:
        if batch.schema_version != EXPECTED_SCHEMA_VERSION:
            raise DataValidationError(
                f"지원하지 않는 상품 스키마입니다: {batch.schema_version} (필요: {EXPECTED_SCHEMA_VERSION})"
            )
        products = tuple(batch.products)
        published = tuple(p for p in products if p.status.casefold() == "published")
        if not published:
            raise DataValidationError("게시 상태의 상품이 0건입니다.")
        seen: set[str] = set()
        duplicates: set[str] = set()
        missing: list[str] = []
        for product in products:
            key = product.computed_lookup_key
            if key in seen:
                duplicates.add(key)
            seen.add(key)
            required = {
                "FNSKU": product.normalized_fnsku,
                "국가코드": product.normalized_country_code,
                "LookupKey": product.lookup_key,
            }
            if product.status.casefold() == "published":
                required.update(
                    {
                        "품목코드": product.item_code,
                        "SKU": product.sku,
                        "품목명": product.product_name,
                    }
                )
            absent = [name for name, value in required.items() if not str(value).strip()]
            if absent:
                missing.append(f"{key or '(빈 FNSKU)'}: {', '.join(absent)}")
            if product.schema_version != batch.schema_version:
                missing.append(f"{key}: 스키마 버전 불일치")
            if product.data_version != batch.data_version:
                missing.append(f"{key}: 데이터 버전 불일치")
            if product.lookup_key.strip().upper() != key:
                missing.append(f"{key}: LookupKey 불일치 ({product.lookup_key or '빈 값'})")
        if duplicates:
            sample = ", ".join(sorted(duplicates)[:10])
            raise DuplicateProductKeyError(f"동일 FNSKU+국가가 2건 이상 있습니다: {sample}")
        if missing:
            raise DataValidationError("필수 상품정보가 누락되었습니다: " + "; ".join(missing[:10]))
        return products

    @staticmethod
    @contextmanager
    def _connect(path: Path, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        else:
            conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _write_database(
        self, path: Path, products: Iterable[Product], batch: ProductBatch, synced_at: str
    ) -> None:
        with self._connect(path) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = DELETE;
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE products (
                    fnsku TEXT NOT NULL,
                    normalized_fnsku TEXT NOT NULL,
                    item_code TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    country_code TEXT NOT NULL,
                    country_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    product_name_en TEXT NOT NULL DEFAULT '',
                    amazon_account TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    source_modified_at TEXT NOT NULL DEFAULT '',
                    data_version TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    lookup_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX idx_products_fnsku ON products(normalized_fnsku);
                CREATE INDEX idx_products_country ON products(country_code);
                CREATE UNIQUE INDEX idx_products_lookup_key ON products(lookup_key);
                """
            )
            conn.executemany(
                """
                INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        p.normalized_fnsku,
                        p.normalized_fnsku,
                        p.item_code,
                        p.sku,
                        p.normalized_country_code,
                        p.country_name,
                        p.product_name,
                        p.product_name_en,
                        p.amazon_account,
                        p.status,
                        p.source_modified_at,
                        p.data_version,
                        p.schema_version,
                        p.computed_lookup_key,
                    )
                    for p in products
                ],
            )
            conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [
                    ("data_version", batch.data_version),
                    ("schema_version", str(batch.schema_version)),
                    ("synced_at", synced_at),
                ],
            )

    def _integrity_check(self, path: Path, expected_count: int) -> None:
        with self._connect(path, read_only=True) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if result != "ok" or count != expected_count:
            raise DataValidationError("새 상품DB 무결성 검사에 실패했습니다.")
