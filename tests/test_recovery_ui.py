import os
import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    from beyondpack.history import JobHistoryDialog
    from beyondpack.ui import MainWindow
except ImportError:
    if os.name == "nt":
        # The Windows post-build gate must not silently skip a broken Qt install.
        if importlib.util.find_spec("PySide6") is not None:
            raise
    QApplication = None

from beyondpack.cache import ProductCacheRepository
from beyondpack.config import AppConfig
from beyondpack.models import BoxItem, Product
from beyondpack.packaging import PackagingRepository
from beyondpack.sources.base import ProductBatch


@unittest.skipIf(QApplication is None, "Qt runtime unavailable; exercised by Windows post-build gate")
class RecoveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = AppConfig(data_dir=str(self.root), operator_name="작업자")
        self.config.label.auto_print = False
        self.cache = ProductCacheRepository(self.root)
        self.products = tuple(Product(code, code, code, "US", "US", code,
            data_version="V1", lookup_key=code + "|US") for code in ("X1", "X2"))
        self.cache.replace_snapshot(ProductBatch(self.products, "V1", 2))
        self.repo = PackagingRepository(self.root / "packaging.db")
        self.windows = []
        self.backup_patch = patch.object(MainWindow, "run_backup", return_value=False)
        self.backup_patch.start()
        self.window = self.open_window()

    def tearDown(self):
        for window in reversed(self.windows):
            window.autosave_timer.stop()
            window.backup_timer.stop()
            window.backup_after_confirm.stop()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.backup_patch.stop()
        self.temp.cleanup()

    def open_window(self):
        window = MainWindow(self.config, self.root / "config.json", self.cache, self.repo,
            lambda _: None, auto_sync=False)
        self.windows.append(window)
        return window

    def fill(self):
        window = self.window
        window.shipment_input.setText("SHIP-1")
        window._select_country("US")
        window.items = [BoxItem.from_product(self.products[0], 2)]
        window._refresh_items_table()
        window.box_count.setValue(2)
        for widget in (window.weight, window.length, window.width, window.height):
            widget.setValue(3)
        window._save_draft()

    def confirm(self):
        self.fill()
        self.window.confirm_box_group()
        self.assertEqual(len(self.repo.shipment_groups("SHIP-1")), 1)

    def test_restart_reopens_exact_job_and_last_label_without_empty_draft_prompt(self):
        self.confirm()
        job_id = self.window.job_id
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as question:
            reopened = self.open_window()
        question.assert_not_called()
        self.assertEqual(reopened.job_id, job_id)
        self.assertEqual(reopened._shipment_code(), "SHIP-1")
        self.assertIsNotNone(reopened.last_saved)
        self.assertEqual(reopened.progress_table.rowCount(), 1)

    def begin_edit(self):
        self.window.progress_table.selectRow(0)
        with patch("beyondpack.ui.QInputDialog.getText", return_value=("수량 정정", True)):
            self.window.amend_selected_group()
        self.assertTrue(self.window.edit_group_id)

    def test_edit_restart_then_confirm_preserves_original_until_commit(self):
        self.confirm()
        original_id = self.window.last_saved[0]["box_group_id"]
        self.begin_edit()
        self.assertIsNotNone(self.repo.box_group(original_id))
        self.window.box_count.setValue(3)
        self.window._save_draft()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            reopened = self.open_window()
        self.assertEqual(reopened.edit_group_id, original_id)
        self.assertEqual(reopened.box_count.value(), 3)
        reopened.confirm_box_group()
        self.assertIsNone(self.repo.box_group(original_id))
        groups = self.repo.shipment_groups("SHIP-1")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["box_count"], 3)
        self.assertEqual(len(self.repo.revision_history()), 1)

    def test_abandoning_edit_keeps_original_without_revision(self):
        self.confirm()
        original_id = self.window.last_saved[0]["box_group_id"]
        self.begin_edit()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.reset_current()
        self.assertIsNotNone(self.repo.box_group(original_id))
        self.assertEqual(self.repo.revision_history(), [])
        self.assertEqual(self.window.edit_group_id, "")

    def test_inactive_product_blocks_real_confirm_and_retains_draft(self):
        self.fill()
        updated = (replace(self.products[0], status="Inactive", data_version="V2"),
            replace(self.products[1], data_version="V2"))
        self.cache.replace_snapshot(ProductBatch(updated, "V2", 2))
        self.window.confirm_box_group()
        self.assertEqual(self.repo.shipment_groups("SHIP-1"), [])
        self.assertIn("사용중지", self.window.next_action.text())
        self.assertIsNotNone(self.repo.load_draft(self.window.DRAFT_KEY))

    def test_changed_product_requires_refresh_before_confirm(self):
        self.fill()
        updated = (replace(self.products[0], sku="NEW", data_version="V2"),
            replace(self.products[1], data_version="V2"))
        self.cache.replace_snapshot(ProductBatch(updated, "V2", 2))
        self.window.confirm_box_group()
        self.assertEqual(self.repo.shipment_groups("SHIP-1"), [])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.refresh_current_products()
        self.window.confirm_box_group()
        row = self.repo.shipment_rows("SHIP-1")[0]
        self.assertEqual(row["sku"], "NEW")
        self.assertEqual(row["product_db_version"], "V2")

    def test_finish_and_history_show_completed_job_without_resuming_it(self):
        self.confirm()
        job_id = self.window.job_id
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.finish_job()
        self.assertEqual(self.repo.job(job_id)["status"], "COMPLETED")
        dialog = JobHistoryDialog(self.repo, self.window)
        dialog.table.selectRow(0)
        self.assertEqual(dialog.group_table.rowCount(), 1)
        self.assertFalse(dialog.resume.isEnabled())
        dialog.deleteLater()

    def test_history_shows_cancelled_original_but_no_active_boxes(self):
        self.confirm()
        self.window.progress_table.selectRow(0)
        with patch("beyondpack.ui.QInputDialog.getText", return_value=("취소", True)):
            self.window.delete_selected_group()
        dialog = JobHistoryDialog(self.repo, self.window)
        dialog.table.selectRow(0)
        self.assertEqual(dialog.group_table.rowCount(), 0)
        self.assertEqual(dialog.revision_table.rowCount(), 1)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
