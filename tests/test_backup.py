import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from beyondpack.backup import (
    CSV_PREFIX,
    CSV_SUFFIX,
    DATABASE_NAME,
    PackagingBackup,
    local_day_bounds,
    station_name,
)
from beyondpack.config import BackupSettings
from beyondpack.models import BoxGroupInput, BoxItem
from beyondpack.packaging import PackagingRepository


class StationNameTests(unittest.TestCase):
    def test_windows_computer_name_is_used_as_is(self):
        self.assertEqual(station_name("PACK-01"), "PACK-01")

    def test_characters_that_break_paths_are_replaced(self):
        self.assertEqual(station_name("포장 PC\\1"), "포장-PC-1")

    def test_korean_computer_names_stay_distinct(self):
        # 한글을 지워 버리면 작업대 이름이 모두 같아져 백업이 서로 덮어쓴다.
        self.assertNotEqual(station_name("포장1층"), station_name("포장2층"))

    def test_empty_name_falls_back(self):
        self.assertEqual(station_name(""), "PC")
        self.assertEqual(station_name("///"), "PC")


class LocalDayBoundsTests(unittest.TestCase):
    def test_bounds_cover_exactly_one_local_day(self):
        start, end = local_day_bounds(datetime.now(timezone.utc))
        opened = datetime.fromisoformat(start)
        closed = datetime.fromisoformat(end)
        self.assertEqual(closed - opened, timedelta(days=1))
        self.assertEqual(opened.astimezone().hour, 0)

    def test_bounds_match_the_stored_timestamp_format(self):
        start, _end = local_day_bounds(datetime.now(timezone.utc))
        # created_at은 초 단위 UTC ISO다. 형식이 같아야 문자열 비교가 성립한다.
        self.assertTrue(start.endswith("+00:00"))
        self.assertEqual(len(start), len("2026-08-25T00:00:00+00:00"))


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / "share"
        self.repo = PackagingRepository(self.root / "data" / "packaging.db")
        self.settings = BackupSettings(directory=str(self.share))
        self.backup = PackagingBackup(self.repo, self.settings, station="PACK-01")

    def tearDown(self):
        self.temp.cleanup()

    def _confirm(self, box_count: int = 3, shipment: str = "1B") -> None:
        job = self.repo.create_job("반장", "V1", "2.2.8", shipment)
        self.repo.save_box_group(
            job,
            BoxGroupInput(
                box_count,
                Decimal("10.0"),
                Decimal("40"),
                Decimal("30"),
                Decimal("25"),
                (BoxItem("X1", "A1", "SKU1", "US", "미국", "상품1", 15),),
            ),
            "반장",
        )

    @property
    def target(self) -> Path:
        return self.share / "PACK-01"

    def test_backup_writes_a_database_copy_and_a_daily_csv(self):
        self._confirm()
        result = self.backup.run()
        self.assertTrue(result.ok, result.message)
        self.assertTrue((self.target / DATABASE_NAME).exists())
        today = f"{datetime.now().astimezone():%Y%m%d}"
        self.assertTrue((self.target / f"{CSV_PREFIX}{today}{CSV_SUFFIX}").exists())

    def test_the_copied_database_alone_restores_the_packing_record(self):
        self._confirm(3)
        self._confirm(2)
        self.backup.run()
        restored = PackagingRepository(self.target / DATABASE_NAME)
        groups = restored.shipment_groups("1B")
        self.assertEqual(
            [(g["box_start_no"], g["box_count"]) for g in groups], [(1, 3), (4, 2)]
        )

    def test_daily_csv_is_readable_by_excel_and_carries_the_shipment(self):
        self._confirm()
        self.backup.run()
        today = f"{datetime.now().astimezone():%Y%m%d}"
        path = self.target / f"{CSV_PREFIX}{today}{CSV_SUFFIX}"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0][0], "출고건")
        self.assertIn("박스확정시각", rows[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "1B")

    def test_an_unreachable_share_reports_failure_without_raising(self):
        backup = PackagingBackup(
            self.repo,
            BackupSettings(directory=str(self.root / "data" / "packaging.db" / "안됨")),
            station="PACK-01",
        )
        result = backup.run()
        self.assertFalse(result.ok)
        self.assertIn("백업 실패", result.message)

    def test_backup_is_skipped_when_no_location_is_set(self):
        backup = PackagingBackup(self.repo, BackupSettings(directory=""), station="PACK-01")
        self.assertFalse(backup.run().ok)
        disabled = PackagingBackup(
            self.repo, BackupSettings(directory=str(self.share), enabled=False), "PACK-01"
        )
        self.assertFalse(disabled.run().ok)
        self.assertFalse(self.share.exists())

    def test_stations_do_not_overwrite_each_other(self):
        self._confirm()
        self.backup.run()
        PackagingBackup(self.repo, self.settings, station="PACK-02").run()
        self.assertTrue((self.share / "PACK-01" / DATABASE_NAME).exists())
        self.assertTrue((self.share / "PACK-02" / DATABASE_NAME).exists())

    def test_old_daily_files_are_pruned_and_the_database_copy_is_kept(self):
        self._confirm()
        self.backup.run()
        stale = self.target / f"{CSV_PREFIX}20200101{CSV_SUFFIX}"
        stale.write_text("old", encoding="utf-8")
        unrelated = self.target / "메모.txt"
        unrelated.write_text("keep", encoding="utf-8")
        self.backup.run()
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue((self.target / DATABASE_NAME).exists())

    def test_repeated_backups_leave_no_temporary_files(self):
        self._confirm()
        for _ in range(3):
            self.assertTrue(self.backup.run().ok)
        self.assertEqual(list(self.target.glob(".beyondpack*")), [])


class RowsBetweenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = PackagingRepository(Path(self.temp.name) / "packaging.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_only_boxes_confirmed_inside_the_window_are_returned(self):
        job = self.repo.create_job("반장", "V1", "2.2.8", "1B")
        self.repo.save_box_group(
            job,
            BoxGroupInput(
                1,
                Decimal("10.0"),
                Decimal("40"),
                Decimal("30"),
                Decimal("25"),
                (BoxItem("X1", "A1", "SKU1", "US", "미국", "상품1", 15),),
            ),
            "반장",
        )
        start, end = local_day_bounds(datetime.now(timezone.utc))
        self.assertEqual(len(self.repo.rows_between(start, end)), 1)
        self.assertEqual(
            self.repo.rows_between("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
