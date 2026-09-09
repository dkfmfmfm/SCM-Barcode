import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_recovery_ui import QApplication, RecoveryUIHarness

if QApplication is not None:
    from PySide6.QtCore import Qt, QPointF, QPoint
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    from beyondpack.help import HelpDialog
    from beyondpack.history import JobHistoryDialog
    from beyondpack.presentation import word_lines


class ExperienceUITests(RecoveryUIHarness):
    def show_window(self, width=1280, height=860):
        self.window.resize(width, height)
        self.window.show()
        for _ in range(8):
            self.app.processEvents()

    def test_enter_commits_typed_quantity_and_returns_to_scan(self):
        self.show_window()
        self.window._select_country("US")
        self.window.fnsku_input.setText("X1")
        self.window.lookup_product()
        self.window.qty_input.lineEdit().setText("24 EA")
        QTest.keyClick(self.window.qty_input.lineEdit(), Qt.Key_Return)
        self.assertEqual(self.window.items[0].qty_per_box, 24)
        self.assertTrue(self.window.fnsku_input.hasFocus())
        self.assertEqual(len(self.repo.load_draft(self.window.DRAFT_KEY)["items"]), 1)

    def test_quantity_edit_updates_draft_and_confirmation_totals(self):
        self.fill()
        self.window.items_table.selectRow(0)
        with patch("beyondpack.ui.QInputDialog.getInt", return_value=(24, True)):
            self.window.edit_item_quantity()
        self.assertEqual(self.window.items[0].qty_per_box, 24)
        self.assertEqual(self.repo.load_draft(self.window.DRAFT_KEY)["items"][0]["qty_per_box"], 24)
        self.assertIn("총 48개", self.window.packing_summary.text())
        self.assertIn("6.000 kg", self.window.packing_summary.text())

    def test_missing_measurement_focuses_field_without_saving(self):
        self.fill()
        self.window.weight.setValue(0)
        self.show_window()
        self.window.confirm_box_group()
        self.assertTrue(self.window.weight.hasFocus())
        self.assertEqual(self.repo.shipment_groups("SHIP-1"), [])

    def test_unfocused_wheel_cannot_change_quantity(self):
        self.show_window()
        self.window.fnsku_input.setFocus()
        before = self.window.qty_input.value()
        wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        self.app.sendEvent(self.window.qty_input, wheel)
        self.assertEqual(self.window.qty_input.value(), before)

    def test_korean_words_wrap_without_clipping_when_width_changes(self):
        label = self.window.product_fields["product_name"]
        text = "집중 보습 세럼 기획 세트 피부 장벽 케어를 위한 데일리 솔루션 " * 3
        label.setText(text.strip())
        self.show_window(1024, 700)
        narrow_height = label.height()
        narrow_lines = QLabel.text(label).splitlines()
        self.assertGreater(len(narrow_lines), 1)
        self.assertEqual(" ".join(narrow_lines), text.strip())
        self.assertEqual(label.text(), text.strip())
        self.show_window(1600, 1000)
        self.assertLessEqual(label.height(), narrow_height)
        self.assertGreater(len(QLabel.text(label).splitlines()[0]), len(narrow_lines[0]))
        metrics = label.fontMetrics()
        words = "상품정보를 확인하고 박스당 수량을 입력하세요."
        self.assertEqual(" ".join(word_lines(words, metrics, 200)), words)

    def test_compact_window_keeps_confirm_and_feedback_visible_and_paints_table(self):
        self.fill()
        self.window.items[0] = replace(self.window.items[0], product_name="피부 장벽을 위한 데일리 보습 세럼 기획 세트 " * 4)
        self.window._refresh_items_table()
        self.show_window(1024, 700)
        self.window.body_scroll.verticalScrollBar().setValue(99999)
        self.app.processEvents()
        for widget in (self.window.confirm_button, self.window.next_action, self.window.packing_summary):
            rect = widget.rect().translated(widget.mapTo(self.window, QPoint(0, 0)))
            self.assertTrue(self.window.rect().contains(rect), widget.objectName())
        self.assertEqual(self.window.body_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(self.window.items_table.rowHeight(0), 38)
        # Actual rendering catches delegate failures invisible to state-only tests.
        shot = self.window.grab()
        self.assertFalse(shot.isNull())
        self.show_window(900, 600)
        self.assertEqual(self.window.body_splitter.orientation(), Qt.Vertical)
        self.assertEqual(self.window.body_scroll.horizontalScrollBar().maximum(), 0)
        self.assertTrue(self.window.confirm_button.isVisible())
        output = os.environ.get("BEYONDPACK_QA_DIR")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            shot.save(str(Path(output) / "compact-workstation.png"))

    def test_help_search_empty_result_and_navigation_are_offline(self):
        dialog = HelpDialog(self.window)
        dialog.search.setText("정정 원본")
        self.assertGreater(dialog.topics.count(), 0)
        self.assertIn("정정", dialog.browser.toPlainText().replace("\u2060", ""))
        dialog.search.setText("NO-SUCH-TOPIC-999")
        self.assertEqual(dialog.topics.count(), 0)
        self.assertIn("일치하는 안내가 없습니다", dialog.browser.toPlainText())
        dialog.search.clear()
        self.assertEqual(dialog.topics.count(), 11)
        dialog.show()
        self.app.processEvents()
        self.assertFalse(dialog.grab().isNull())

    def test_history_invalid_date_range_clears_results_and_actions(self):
        self.confirm()
        dialog = JobHistoryDialog(self.repo, self.window)
        self.assertTrue(dialog.resume.isEnabled())
        dialog.period.setCurrentIndex(3)
        dialog.start.setDate(dialog.end.date().addDays(1))
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.resume.isEnabled())
        self.assertFalse(dialog.export.isEnabled())
        self.assertIn("시작일", dialog.result_count.text())
