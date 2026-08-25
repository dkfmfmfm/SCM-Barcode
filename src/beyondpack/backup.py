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

from .config import BackupSettings
from .exporter import FIELDS, HEADERS
from .models import utc_now_iso
from .packaging import PackagingRepository


# 경로에서 문제가 되는 문자만 바꾼다. 한글 PC 이름을 통째로 지우면
# 작업대마다 이름이 "PC"로 같아져 서로 덮어쓴다.
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')

DATABASE_NAME = "packaging.db"
CSV_PREFIX = "packing-"
CSV_SUFFIX = ".csv"


def station_name(raw: str | None = None) -> str:
    """백업 폴더를 작업대별로 나누는 이름. 여러 PC가 서로 덮어쓰지 않게 한다."""
    cleaned = _UNSAFE_NAME.sub("-", raw if raw is not None else platform.node() or "")
    return cleaned.strip("-")[:40] or "PC"


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
