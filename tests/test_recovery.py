import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from beyondpack.backup import BackupRunner, PackagingBackup, restore_packaging_backup
from beyondpack.cache import ProductCacheRepository
from beyondpack.config import BackupSettings
from beyondpack.errors import InactiveProductError, PackagingValidationError, ProductNotFoundError
from beyondpack.models import BoxGroupInput, BoxItem, Product
from beyondpack.packaging import PackagingRepository
from beyondpack.sources.base import ProductBatch


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = PackagingRepository(self.root / "packaging.db")
        self.cache = ProductCacheRepository(self.root)
        self.products = tuple(Product(code, code, code, "US", "US", code,
            data_version="V1", lookup_key=code + "|US") for code in ("X1", "X2"))
        self.cache.replace_snapshot(ProductBatch(self.products, "V1", 2))
        self.item = BoxItem.from_product(self.products[0], 2)
        self.value = BoxGroupInput(2, Decimal("3"), Decimal("4"), Decimal("5"), Decimal("6"), (self.item,))
        self.job = self.repo.create_job("반장", "V1", "2.3.0", "SHIP-1")

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **kwargs):
        return self.repo.save_box_group(self.job, self.value, "작업자", **kwargs)

    def test_cancel_keeps_full_original_and_is_not_counted_twice(self):
        saved = self.save()
        self.repo.take_back_box_group(saved.box_group_id, "반장", "오포장", "CANCEL")
        with self.assertRaises(PackagingValidationError):
            self.repo.take_back_box_group(saved.box_group_id, "반장", "중복 취소", "CANCEL")
        revisions = self.repo.revision_history(self.job)
        self.assertEqual(len(revisions), 1)
        original = json.loads(revisions[0]["snapshot_json"])
        self.assertEqual(original["group"]["height_cm"], "6")
        self.assertEqual(original["items"][0]["qty_per_box"], 2)
        self.assertEqual(self.repo.shipment_rows("SHIP-1"), [])
        self.assertEqual(self.save().box_start_no, 1)

    def test_failed_correction_rolls_back_original_archive_and_draft(self):
        saved = self.save()
        self.repo.save_draft("current", {"job_id": self.job, "edit_group_id": saved.box_group_id})
        broken = replace(self.value, items=(replace(self.item, qty_per_box=0),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_box_group(self.job, broken, "작업자", replaces_group_id=saved.box_group_id,
                reason="수량 수정", draft_key="current")
        self.assertIsNotNone(self.repo.box_group(saved.box_group_id))
        self.assertEqual(self.repo.revision_history(), [])
        self.assertIsNotNone(self.repo.load_draft("current"))

    def test_correction_links_old_and_new_and_consumes_draft_atomically(self):
        saved = self.save()
        self.repo.save_draft("current", {"job_id": self.job})
        corrected = self.save(replaces_group_id=saved.box_group_id, reason="규격 수정",
            draft_key="current", product_db_version="V2", verified_at="now")
        revision = self.repo.revision_history(self.job)[0]
        self.assertEqual(revision["replacement_id"], corrected.box_group_id)
        self.assertEqual(revision["state"], "CORRECTED")
        self.assertEqual(corrected.box_start_no, saved.box_start_no)
        self.assertEqual(len(self.repo.shipment_groups("SHIP-1")), 1)
        self.assertIsNone(self.repo.load_draft("current"))
        self.assertEqual(self.repo.load_draft("active-job")["job_id"], self.job)

    def test_correction_of_a_stale_selection_preserves_all_records(self):
        # 중간 박스 정정은 허용하지만, 이미 교체돼 사라진 id로는 막아야 한다.
        # 정정하면 새 box_group_id가 생기므로 옛 id는 더 이상 유효하지 않다.
        old = self.save()
        self.save()
        corrected = self.save(replaces_group_id=old.box_group_id, reason="정정")
        self.assertNotEqual(corrected.box_group_id, old.box_group_id)
        with self.assertRaises(PackagingValidationError):
            self.save(replaces_group_id=old.box_group_id, reason="다시 정정")
        self.assertEqual(len(self.repo.shipment_groups("SHIP-1")), 2)

    def test_correcting_a_middle_box_keeps_its_number(self):
        first = self.save()
        self.save()
        corrected = self.save(replaces_group_id=first.box_group_id, reason="무게 정정")
        self.assertEqual(corrected.box_start_no, first.box_start_no)
        self.assertEqual(corrected.renumbered, ())
        starts = [g["box_start_no"] for g in self.repo.shipment_groups("SHIP-1")]
        self.assertEqual(starts, sorted(starts))

    def test_job_search_resume_complete_and_reopen_database(self):
        self.save()
        reopened = PackagingRepository(self.repo.path)
        self.assertEqual(reopened.search_jobs("ship-1", "OPEN")[0]["job_id"], self.job)
        self.assertEqual(reopened.search_jobs("반장")[0]["box_count"], 2)
        self.assertEqual(reopened.resume_job(self.job, "다음 작업자")["job_id"], self.job)
        reopened.close_job(self.job, "다음 작업자")
        self.assertIsNone(reopened.load_draft("active-job"))
        self.assertEqual(reopened.search_jobs(status="OPEN"), [])
        self.assertEqual(len(reopened.job_rows(self.job)), 1)
        with self.assertRaises(PackagingValidationError):
            reopened.resume_job(self.job, "반장")
        with self.assertRaises(PackagingValidationError):
            reopened.save_box_group(self.job, self.value, "반장")

    def test_completed_job_and_pending_draft_cannot_be_changed(self):
        saved = self.save()
        self.repo.save_draft("current", {"job_id": self.job})
        with self.assertRaises(PackagingValidationError):
            self.repo.close_job(self.job, "반장")
        self.repo.clear_draft("current")
        self.repo.close_job(self.job, "반장")
        with self.assertRaises(PackagingValidationError):
            self.repo.take_back_box_group(saved.box_group_id, "반장", "실수")

    def update(self, **changes):
        products = (replace(self.products[0], data_version="V2", **changes), replace(self.products[1], data_version="V2"))
        self.cache.replace_snapshot(ProductBatch(products, "V2", 2))

    def test_inactive_and_changed_previously_selected_products_are_blocked(self):
        self.update(status="Inactive")
        with self.assertRaises(InactiveProductError), self.cache.validated_items((self.item,)):
            self.fail("inactive product accepted")
        self.update(sku="CHANGED")
        with self.assertRaises(PackagingValidationError), self.cache.validated_items((self.item,)):
            self.fail("changed product accepted")
        self.assertEqual(self.repo.job_rows(self.job), [])

    def test_removed_product_is_blocked(self):
        self.cache.replace_snapshot(ProductBatch((replace(self.products[1], data_version="V2"),), "V2", 2), drop_threshold=.9)
        with self.assertRaises(ProductNotFoundError), self.cache.validated_items((self.item,)):
            self.fail("removed product accepted")

    def test_each_box_records_the_validated_version_and_actual_operator(self):
        self.save(product_db_version="V1")
        self.update(source_modified_at="2026-09-08T00:00:00Z")
        with self.cache.validated_items((self.item,)) as (items, info):
            self.repo.save_box_group(self.job, replace(self.value, items=items), "다음 작업자",
                product_db_version=info.data_version)
        rows = self.repo.job_rows(self.job)
        self.assertEqual([r["product_db_version"] for r in rows], ["V1", "V2"])
        self.assertEqual(rows[1]["operator_name"], "다음 작업자")
        self.assertEqual(rows[1]["source_modified_at"], "2026-09-08T00:00:00Z")

    def test_sync_cannot_switch_snapshot_during_validation_and_save(self):
        entered, done = threading.Event(), threading.Event()
        def sync():
            entered.set()
            self.update(status="Inactive")
            done.set()
        with self.cache.validated_items((self.item,)) as (_, info):
            thread = threading.Thread(target=sync)
            thread.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(done.wait(.05))
            self.save(product_db_version=info.data_version)
        thread.join(3)
        self.assertTrue(done.is_set())

    def test_local_backup_runs_without_shared_folder_and_keeps_hourly_copies(self):
        self.save()
        runner = BackupRunner(self.repo, self.cache, BackupSettings(), "PACK")
        now = datetime.now(timezone.utc)
        self.assertTrue(runner.run(now).ok)
        self.save()
        self.assertTrue(runner.run(now + timedelta(hours=1)).ok)
        target = self.root / "backups" / "PACK"
        self.assertEqual(len(list(target.glob("packaging-*.db"))), 2)
        self.assertTrue((target / "packaging.db").exists())

    def test_restore_validates_and_preserves_pre_restore_records(self):
        first = self.save()
        backup = PackagingBackup(self.repo, BackupSettings(directory=str(self.root / "share")), "PACK")
        self.assertTrue(backup.run().ok)
        self.save()
        safety = restore_packaging_backup(self.repo, backup.target / "packaging.db", "복원자")
        self.assertEqual(len(self.repo.shipment_groups("SHIP-1")), 1)
        self.assertIsNotNone(self.repo.box_group(first.box_group_id))
        self.assertEqual(len(PackagingRepository(safety).shipment_groups("SHIP-1")), 2)
        self.assertEqual(self.repo.audit_events()[0]["action"], "RESTORE")

    def test_invalid_backup_leaves_live_records_unchanged(self):
        self.save()
        wrong = self.root / "wrong.db"
        with closing(sqlite3.connect(wrong)) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaises(ValueError):
            restore_packaging_backup(self.repo, wrong, "반장")
        self.assertEqual(len(self.repo.shipment_groups("SHIP-1")), 1)

    def test_archive_and_correction_history_survive_backup_restore(self):
        old = self.save()
        self.save(replaces_group_id=old.box_group_id, reason="정정")
        backup = PackagingBackup(self.repo, BackupSettings(directory=str(self.root / "share")), "PACK")
        self.assertTrue(backup.run().ok)
        restore_packaging_backup(self.repo, backup.target / "packaging.db", "반장")
        self.assertEqual(self.repo.revision_history()[0]["state"], "CORRECTED")


if __name__ == "__main__":
    unittest.main()
