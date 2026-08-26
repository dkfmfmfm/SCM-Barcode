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
    MIN_PRODUCT_COPIES,
    PRODUCT_DIR,
    PRODUCT_PREFIX,
    PRODUCT_SUFFIX,
    BackupRunner,
    PackagingBackup,
    ProductMasterBackup,
    local_day_bounds,
    station_name,
    version_tag,
)
from beyondpack.cache import ProductCacheRepository
from beyondpack.config import BackupSettings
from beyondpack.models import BoxGroupInput, BoxItem, Product
from beyondpack.packaging import PackagingRepository
from beyondpack.sources.base import ProductBatch
from beyondpack.sources.excel_source import ExcelProductSource


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


def sheet_product(fnsku: str, version: str, country_code: str = "US") -> Product:
    return Product(
        fnsku=fnsku,
        item_code="A000000120",
        sku="BE-SKU-1",
        country_code=country_code,
        country_name="미국",
        product_name="동원 양반김 12팩",
        product_name_en="DONGWON SEAWEED 12 PACK",
        amazon_account="BEYOND-US",
        status="Published",
        source_modified_at="2026-08-11T00:00:00Z",
        data_version=version,
        schema_version=2,
        lookup_key=f"{fnsku}|{country_code}",
    )


class VersionTagTests(unittest.TestCase):
    def test_path_hostile_characters_are_replaced(self):
        self.assertEqual(version_tag("AUTO-1A2B"), "AUTO-1A2B")
        self.assertEqual(version_tag("2026/08/26 v1"), "2026-08-26-v1")

    def test_an_empty_version_still_produces_a_name(self):
        self.assertEqual(version_tag(""), "NOVERSION")
        self.assertEqual(version_tag("///"), "NOVERSION")


class ProductMasterBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / "share"
        self.cache = ProductCacheRepository(self.root / "data")
        self.settings = BackupSettings(directory=str(self.share))
        self.backup = ProductMasterBackup(self.cache, self.settings)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def target(self) -> Path:
        return self.share / PRODUCT_DIR

    def _publish(self, version: str, *fnskus: str) -> None:
        products = tuple(sheet_product(fnsku, version) for fnsku in fnskus)
        self.cache.replace_snapshot(ProductBatch(products, version, 2))

    def _copies(self) -> list[str]:
        return sorted(p.name for p in self.target.glob(f"*{PRODUCT_SUFFIX}"))

    def test_the_active_snapshot_is_copied_off_the_workstation(self):
        self._publish("V1", "X003ABC123", "X004DEF456")
        result = self.backup.run()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, 2)
        copies = self._copies()
        self.assertEqual(len(copies), 1)
        self.assertTrue(copies[0].startswith(PRODUCT_PREFIX))
        self.assertTrue(copies[0].endswith(f"-V1{PRODUCT_SUFFIX}"))

    def test_the_copy_alone_restores_the_product_master(self):
        # Google Sheet를 잃었을 때 이 파일 하나로 되돌릴 수 있어야 한다.
        self._publish("V1", "X003ABC123", "X004DEF456")
        self.backup.run()
        path = self.target / self._copies()[0]
        batch = ExcelProductSource(path).fetch_products()
        self.assertEqual(batch.data_version, "V1")
        self.assertEqual(batch.schema_version, 2)
        restored = ProductCacheRepository(self.root / "restored")
        self.assertEqual(restored.replace_snapshot(batch).product_count, 2)
        found = restored.lookup("x003abc123", "us")
        self.assertEqual(found.item_code, "A000000120")
        self.assertEqual(found.sku, "BE-SKU-1")
        self.assertEqual(found.product_name, "동원 양반김 12팩")
        self.assertEqual(found.amazon_account, "BEYOND-US")

    def test_unchanged_products_are_not_copied_again(self):
        self._publish("V1", "X003ABC123")
        first = self.backup.run()
        path = self.target / self._copies()[0]
        written = path.stat().st_mtime_ns
        second = self.backup.run()
        self.assertTrue(second.ok, second.message)
        self.assertEqual(self._copies(), [path.name])
        self.assertEqual(path.stat().st_mtime_ns, written)
        self.assertIn("보관", first.message)
        self.assertIn("유지", second.message)

    def test_a_changed_sheet_is_kept_beside_the_previous_copy(self):
        self._publish("V1", "X003ABC123")
        self.backup.run()
        self._publish("V2", "X003ABC123", "X004DEF456")
        self.backup.run()
        copies = self._copies()
        self.assertEqual(len(copies), 2)
        self.assertTrue(any(name.endswith(f"-V1{PRODUCT_SUFFIX}") for name in copies))
        self.assertTrue(any(name.endswith(f"-V2{PRODUCT_SUFFIX}") for name in copies))

    def test_stations_sharing_one_location_do_not_duplicate_the_master(self):
        self._publish("V1", "X003ABC123")
        self.backup.run()
        ProductMasterBackup(self.cache, self.settings).run()
        self.assertEqual(len(self._copies()), 1)

    def test_a_new_pc_without_a_product_db_is_not_a_failure(self):
        result = self.backup.run()
        self.assertTrue(result.ok, result.message)
        self.assertIn("상품DB가 없어", result.message)
        self.assertFalse(self.target.exists())

    def test_backup_is_skipped_when_no_location_is_set(self):
        self._publish("V1", "X003ABC123")
        self.assertFalse(ProductMasterBackup(self.cache, BackupSettings()).run().ok)
        self.assertFalse(self.share.exists())

    def test_an_unreachable_share_reports_failure_without_raising(self):
        self._publish("V1", "X003ABC123")
        blocked = self.root / "data" / "packaging.db"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("not a directory", encoding="utf-8")
        backup = ProductMasterBackup(
            self.cache, BackupSettings(directory=str(blocked / "안됨"))
        )
        result = backup.run()
        self.assertFalse(result.ok)
        self.assertIn("상품 마스터 백업 실패", result.message)

    def test_the_last_copy_survives_even_when_the_sheet_is_never_edited(self):
        # 보관일수만으로 지우면 시트를 오래 고치지 않은 현장의 유일한 사본이 사라진다.
        self._publish("V1", "X003ABC123")
        self.backup.run()
        only = self.target / self._copies()[0]
        stale = self.target / f"{PRODUCT_PREFIX}20200101-OLD{PRODUCT_SUFFIX}"
        stale.write_bytes(only.read_bytes())
        self.backup.run()
        self.assertTrue(only.exists())
        self.assertTrue(stale.exists())

    def test_copies_beyond_the_kept_minimum_are_pruned_by_age(self):
        self._publish("V1", "X003ABC123")
        self.backup.run()
        seed = (self.target / self._copies()[0]).read_bytes()
        for index in range(MIN_PRODUCT_COPIES + 5):
            name = f"{PRODUCT_PREFIX}2020{index // 28 + 1:02d}{index % 28 + 1:02d}-OLD{index}{PRODUCT_SUFFIX}"
            (self.target / name).write_bytes(seed)
        unrelated = self.target / "메모.txt"
        unrelated.write_text("keep", encoding="utf-8")
        self.backup.run()
        self.assertEqual(len(self._copies()), MIN_PRODUCT_COPIES)
        self.assertTrue(unrelated.exists())

    def test_repeated_backups_leave_no_temporary_files(self):
        self._publish("V1", "X003ABC123")
        for _ in range(3):
            self.assertTrue(self.backup.run().ok)
        self.assertEqual(list(self.target.glob(".beyondpack*")), [])


class BackupRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / "share"
        self.repo = PackagingRepository(self.root / "data" / "packaging.db")
        self.cache = ProductCacheRepository(self.root / "data")
        self.settings = BackupSettings(directory=str(self.share))

    def tearDown(self):
        self.temp.cleanup()

    def _runner(self) -> BackupRunner:
        return BackupRunner(self.repo, self.cache, self.settings, station="PACK-01")

    def test_one_run_saves_both_the_packing_record_and_the_product_master(self):
        job = self.repo.create_job("반장", "V1", "2.2.10", "1B")
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
        self.cache.replace_snapshot(
            ProductBatch((sheet_product("X003ABC123", "V1"),), "V1", 2)
        )
        result = self._runner().run()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.rows, 1)
        self.assertTrue((self.share / "PACK-01" / DATABASE_NAME).exists())
        self.assertEqual(len(list((self.share / PRODUCT_DIR).glob(f"*{PRODUCT_SUFFIX}"))), 1)

    def test_a_failing_half_is_reported_without_losing_the_other(self):
        self.cache.replace_snapshot(
            ProductBatch((sheet_product("X003ABC123", "V1"),), "V1", 2)
        )
        runner = self._runner()
        runner.records.settings = BackupSettings(directory="")
        result = runner.run()
        self.assertFalse(result.ok)
        self.assertIn("백업 위치가 지정되지 않았습니다", result.message)
        self.assertIn("상품 마스터 보관", result.message)


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
