from __future__ import annotations

import csv
import os
import platform
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cache import CacheInfo, ProductCacheRepository
from .config import BackupSettings
from .exporter import FIELDS, HEADERS
from .models import Product, utc_now_iso
from .packaging import PackagingRepository


# 경로에서 문제가 되는 문자만 바꾼다. 한글 PC 이름을 통째로 지우면
# 작업대마다 이름이 "PC"로 같아져 서로 덮어쓴다.
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')

# 파일 이름에 그대로 쓸 수 없는 DataVersion 문자를 바꾼다.
_UNSAFE_VERSION = re.compile(r"[^A-Za-z0-9._-]+")

DATABASE_NAME = "packaging.db"
CSV_PREFIX = "packing-"
CSV_SUFFIX = ".csv"

PRODUCT_DIR = "products"
PRODUCT_PREFIX = "products-"
PRODUCT_SUFFIX = ".xlsx"

# `Excel 비상 업데이트`가 찾는 시트 이름과 열 이름을 그대로 쓴다. 이 파일 하나로
# 상품 원본을 되돌릴 수 있어야 백업이 의미가 있다.
PRODUCT_SHEET = "BeyondPack"
PRODUCT_HEADERS = (
    "FNSKU",
    "ItemCode",
    "SKU",
    "CountryCode",
    "CountryName",
    "ProductName",
    "ProductNameEn",
    "AmazonAccount",
    "Status",
    "SourceModifiedAt",
    "DataVersion",
    "SchemaVersion",
    "LookupKey",
)
PRODUCT_FIELDS = (
    "fnsku",
    "item_code",
    "sku",
    "country_code",
    "country_name",
    "product_name",
    "product_name_en",
    "amazon_account",
    "status",
    "source_modified_at",
    "data_version",
    "schema_version",
    "lookup_key",
)

# 보관일수만으로 지우면, 시트를 오래 고치지 않은 현장에서 마지막 남은 사본까지
# 지워질 수 있다. 최근 사본은 나이와 무관하게 남긴다.
MIN_PRODUCT_COPIES = 30


def station_name(raw: str | None = None) -> str:
    """백업 폴더를 작업대별로 나누는 이름. 여러 PC가 서로 덮어쓰지 않게 한다."""
    cleaned = _UNSAFE_NAME.sub("-", raw if raw is not None else platform.node() or "")
    return cleaned.strip("-")[:40] or "PC"


def version_tag(data_version: str) -> str:
    """DataVersion을 파일 이름에 쓸 수 있는 형태로 줄인다."""
    cleaned = _UNSAFE_VERSION.sub("-", data_version).strip("-")[:24]
    return cleaned or "NOVERSION"


def local_day_bounds(moment: datetime) -> tuple[str, str]:
    """현장 기준 하루의 시작·끝을 저장 형식(UTC ISO)으로 돌려준다.

    `created_at`은 UTC ISO 고정 형식이라 문자열 비교로 범위를 자를 수 있다.
    """
    start = moment.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


@dataclass(frozen=True, slots=True)
class BackupResult:
    ok: bool
    message: str
    at: str
    rows: int = 0


class PackagingBackup:
    """포장 실적을 작업 PC 밖으로 복사한다.

    `packaging.db`는 이 PC에만 있어 디스크가 고장나면 복구할 수 없다. 지정한
    폴더(공유 드라이브 등)에 DB 사본과 당일 실적 CSV를 남겨, 작업자가 Excel
    저장을 잊더라도 실적이 남게 한다.

    백업이 실패해도 포장 작업은 막지 않는다. 결과만 돌려주고 다음 주기에
    다시 시도한다.
    """

    def __init__(
        self,
        repository: PackagingRepository,
        settings: BackupSettings,
        station: str | None = None,
    ):
        self.repository = repository
        self.settings = settings
        self.station = station_name(station)

    @property
    def target(self) -> Path:
        return Path(self.settings.directory).expanduser() / self.station

    def run(self, now: datetime | None = None) -> BackupResult:
        moment = now or datetime.now(timezone.utc)
        stamp = utc_now_iso() if now is None else moment.isoformat(timespec="seconds")
        if not self.settings.active:
            return BackupResult(False, "백업 위치가 지정되지 않았습니다.", stamp)
        try:
            target = self.target
            target.mkdir(parents=True, exist_ok=True)
            self._copy_database(target)
            rows = self._write_daily_csv(target, moment)
            self._prune(target, moment)
        except (OSError, sqlite3.Error) as exc:
            return BackupResult(False, f"백업 실패: {exc}", stamp)
        return BackupResult(True, f"백업 완료: {target}", stamp, rows)

    def _temp_path(self, target: Path, suffix: str) -> Path:
        return target / f".beyondpack.{os.getpid()}.{uuid.uuid4().hex}{suffix}"

    def _copy_database(self, target: Path) -> None:
        """SQLite 백업 API로 일관된 사본을 만든다.

        파일을 그대로 복사하면 WAL이 반영되지 않은 시점이 섞일 수 있다.
        """
        temp = self._temp_path(target, ".db")
        try:
            with closing(
                sqlite3.connect(f"file:{self.repository.path}?mode=ro", uri=True)
            ) as source, closing(sqlite3.connect(temp)) as destination:
                source.backup(destination)
            os.replace(temp, target / DATABASE_NAME)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _write_daily_csv(self, target: Path, moment: datetime) -> int:
        start, end = local_day_bounds(moment)
        rows = self.repository.rows_between(start, end)
        name = f"{CSV_PREFIX}{moment.astimezone():%Y%m%d}{CSV_SUFFIX}"
        temp = self._temp_path(target, ".csv")
        try:
            # Excel이 한글을 바로 읽도록 BOM을 붙인다.
            with temp.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(HEADERS)
                for row in rows:
                    writer.writerow([row.get(field, "") for field in FIELDS])
            os.replace(temp, target / name)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return len(rows)

    def _prune(self, target: Path, moment: datetime) -> None:
        """보관일수를 넘긴 일자별 CSV만 지운다. DB 사본은 지우지 않는다."""
        oldest = (moment.astimezone() - timedelta(days=self.settings.keep_days)).date()
        for path in target.glob(f"{CSV_PREFIX}*{CSV_SUFFIX}"):
            stamp = path.name[len(CSV_PREFIX) : -len(CSV_SUFFIX)]
            try:
                day = datetime.strptime(stamp, "%Y%m%d").date()
            except ValueError:
                continue
            if day < oldest:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # 정리는 최선 노력이다. 실패해도 백업 자체는 성공으로 둔다.
                    pass


class ProductMasterBackup:
    """상품 마스터를 작업 PC 밖에 보관한다.

    상품 원본은 Google Sheet 한 곳뿐이다. 시트가 지워지거나 잘못 편집되면
    프로그램 쪽에는 되돌릴 수단이 없다. 그래서 검증을 통과해 실제로 쓰고 있는
    스냅샷을 `설정·관리자 도구 > Excel 비상 업데이트`가 그대로 읽을 수 있는
    `.xlsx`로 남긴다. 시트를 잃어도 이 파일 하나로 되돌릴 수 있다.

    내용이 바뀌었을 때만 새 파일을 쓴다. 파일 이름에 `DataVersion`이 들어가고
    이미 있으면 건너뛰므로, 10분마다 돌아도 같은 내용이 쌓이지 않고 여러
    작업대가 같은 위치를 써도 서로 덮어쓰지 않는다.
    """

    def __init__(self, cache: ProductCacheRepository, settings: BackupSettings):
        self.cache = cache
        self.settings = settings

    @property
    def target(self) -> Path:
        return Path(self.settings.directory).expanduser() / PRODUCT_DIR

    def file_name(self, info: CacheInfo) -> str:
        """같은 상품 내용은 언제 백업하든 같은 이름이 되게 한다."""
        return (
            f"{PRODUCT_PREFIX}{self._synced_day(info):%Y%m%d}"
            f"-{version_tag(info.data_version)}{PRODUCT_SUFFIX}"
        )

    @staticmethod
    def _synced_day(info: CacheInfo) -> datetime:
        try:
            moment = datetime.fromisoformat(info.synced_at.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now().astimezone()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone()

    def run(self, now: datetime | None = None) -> BackupResult:
        moment = now or datetime.now(timezone.utc)
        stamp = utc_now_iso() if now is None else moment.isoformat(timespec="seconds")
        if not self.settings.active:
            return BackupResult(False, "백업 위치가 지정되지 않았습니다.", stamp)
        rows = 0
        try:
            # 요약만 먼저 읽는다. 대부분의 백업은 상품이 그대로라 여기서 끝난다.
            info = self.cache.info()
            if not info.product_count:
                # 아직 상품DB가 없는 새 PC다. 백업할 것이 없을 뿐 실패가 아니다.
                return BackupResult(True, "상품DB가 없어 상품 마스터는 건너뜀", stamp)
            self.target.mkdir(parents=True, exist_ok=True)
            path = self.target / self.file_name(info)
            if not path.exists():
                # 이름을 정한 뒤 상품을 읽는다. 그 사이 업데이트가 끝났을 수
                # 있으므로 실제로 읽어 온 내용에 맞춰 이름을 다시 잡는다.
                current, products = self.cache.export_snapshot()
                if not products:
                    return BackupResult(True, "상품DB가 없어 상품 마스터는 건너뜀", stamp)
                path = self.target / self.file_name(current)
                if not path.exists():
                    self._write(path, products)
                    rows = len(products)
            self._prune(self.target, moment)
        except (OSError, ValueError, ImportError, sqlite3.Error) as exc:
            return BackupResult(False, f"상품 마스터 백업 실패: {exc}", stamp)
        if not rows:
            return BackupResult(True, f"상품 마스터 최신본 유지: {path.name}", stamp)
        return BackupResult(True, f"상품 마스터 보관: {path.name}", stamp, rows)

    def _write(self, path: Path, products: tuple[Product, ...]) -> None:
        from openpyxl import Workbook

        # 상품 수가 늘어도 백업이 작업 PC의 메모리를 먹지 않도록 흘려 쓴다.
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(PRODUCT_SHEET)
        sheet.freeze_panes = "A2"
        sheet.append(list(PRODUCT_HEADERS))
        for product in products:
            values = product.to_dict()
            sheet.append([values[field] for field in PRODUCT_FIELDS])
        temp = path.parent / f".beyondpack.{os.getpid()}.{uuid.uuid4().hex}{PRODUCT_SUFFIX}"
        try:
            workbook.save(temp)
            os.replace(temp, path)
        finally:
            workbook.close()
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _prune(self, target: Path, moment: datetime) -> None:
        """오래된 사본만 지우되 최근 것은 나이와 무관하게 남긴다."""
        paths = sorted(
            target.glob(f"{PRODUCT_PREFIX}*{PRODUCT_SUFFIX}"), reverse=True
        )
        oldest = (moment.astimezone() - timedelta(days=self.settings.keep_days)).date()
        for path in paths[MIN_PRODUCT_COPIES:]:
            stamp = path.name[len(PRODUCT_PREFIX) : len(PRODUCT_PREFIX) + 8]
            try:
                day = datetime.strptime(stamp, "%Y%m%d").date()
            except ValueError:
                continue
            if day < oldest:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


class BackupRunner:
    """한 번의 백업으로 포장 실적과 상품 마스터를 함께 남긴다.

    둘 중 하나가 실패해도 나머지는 진행하고, 결과는 합쳐서 한 줄로 보고한다.
    """

    def __init__(
        self,
        repository: PackagingRepository,
        cache: ProductCacheRepository,
        settings: BackupSettings,
        station: str | None = None,
    ):
        self.records = PackagingBackup(repository, settings, station)
        self.master = ProductMasterBackup(cache, settings)

    def run(self, now: datetime | None = None) -> BackupResult:
        records = self.records.run(now)
        master = self.master.run(now)
        return BackupResult(
            records.ok and master.ok,
            f"{records.message} · {master.message}",
            records.at,
            records.rows,
        )
