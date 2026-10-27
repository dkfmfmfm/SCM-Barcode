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
    from beyondpack.models import BoxItem
    from beyondpack.ui import MainWindow


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


class NumericEntryUITests(RecoveryUIHarness):
    """숫자칸을 마우스로 누르고 치면 자리가 뒤집히던 문제."""

    def show_window(self, width=1280, height=860):
        self.window.resize(width, height)
        self.window.show()
        for _ in range(8):
            self.app.processEvents()

    def click_then_type(self, spin, preset, keys, settle: bool):
        spin.setValue(preset)
        self.window.fnsku_input.setFocus()
        self.app.processEvents()
        line = spin.lineEdit()
        QTest.mouseClick(line, Qt.LeftButton, Qt.NoModifier, QPoint(2, line.height() // 2))
        if settle:
            self.app.processEvents()
        for key in keys:
            QTest.keyClicks(line, key)
        self.app.processEvents()
        return spin.value()

    def test_digits_keep_their_order_even_when_typed_immediately(self):
        # 클릭 직후 곧바로 치면 늦은 전체선택과 경합해 12가 21이 됐다.
        self.show_window()
        for settle in (True, False):
            self.assertEqual(self.click_then_type(self.window.box_count, 0, "12", settle), 12)
            self.assertEqual(self.click_then_type(self.window.box_count, 0, "105", settle), 105)
            self.assertEqual(self.click_then_type(self.window.box_count, 3, "12", settle), 12)
            self.assertEqual(self.click_then_type(self.window.weight, 0, "12.5", settle), 12.5)
            self.assertEqual(self.click_then_type(self.window.weight, 9.5, "11", settle), 11.0)
            self.assertEqual(self.click_then_type(self.window.qty_input, 0, "15", settle), 15)

    def test_a_second_click_still_places_the_cursor_for_one_digit_edits(self):
        self.show_window()
        spin = self.window.box_count
        spin.setValue(12)
        self.window.fnsku_input.setFocus()
        self.app.processEvents()
        line = spin.lineEdit()
        QTest.mouseClick(line, Qt.LeftButton, Qt.NoModifier, QPoint(2, line.height() // 2))
        self.app.processEvents()
        line.deselect()
        line.setCursorPosition(2)
        QTest.mouseClick(line, Qt.LeftButton, Qt.NoModifier, QPoint(20, line.height() // 2))
        line.setCursorPosition(2)
        QTest.keyClicks(line, "3")
        self.app.processEvents()
        self.assertEqual(spin.value(), 123)

    def test_the_configured_country_is_selected_without_touching_the_draft(self):
        from PySide6.QtWidgets import QMessageBox

        self.assertEqual(self.window._selected_country_code(), "US")
        # 시작하면서 고른 기본 국가가 복구용 임시저장을 지우면 안 된다.
        self.fill()
        self.assertIsNotNone(self.repo.load_draft(self.window.DRAFT_KEY))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            reopened = self.open_window()
        self.assertEqual(reopened._selected_country_code(), "US")
        self.assertEqual(reopened.box_count.value(), 2, "복구할 입력이 살아 있어야 한다")

    def test_an_unknown_default_country_leaves_the_prompt_in_place(self):
        config = replace(self.config, default_country_code="ZZ")
        window = MainWindow(config, self.root / "config.json", self.cache, self.repo,
            lambda _: None, auto_sync=False)
        self.windows.append(window)
        self.assertEqual(window._selected_country_code(), "")


class ShipmentSwitchingUITests(RecoveryUIHarness):
    """항공·해상처럼 출고건을 오가며 작업하는 흐름."""

    def pack(self, shipment, boxes=2, answer=None):
        window = self.window
        window.shipment_input.setText(shipment)
        self.app.processEvents()
        window._select_country("US")
        window.items = [BoxItem.from_product(self.products[0], 2)]
        window.items_shipment = window._shipment_code()
        window._refresh_items_table()
        window.box_count.setValue(boxes)
        for widget in (window.weight, window.length, window.width, window.height):
            widget.setValue(3)
        if answer is None:
            window.confirm_box_group()
        else:
            with patch("beyondpack.ui.QMessageBox.question", lambda *a, **k: answer):
                window.confirm_box_group()
        self.app.processEvents()

    def test_returning_to_a_shipment_continues_its_open_job(self):
        self.pack("항공-1")
        self.pack("해상-1")
        self.pack("항공-1")
        air = [j for j in self.repo.search_jobs() if j["shipment_code"] == "항공-1"]
        self.assertEqual(len(air), 1, "출고건을 오갈 때마다 작업이 새로 생기면 안 된다")
        self.assertEqual(air[0]["box_count"], 4)
        self.assertEqual(
            [(g["box_start_no"], g["box_count"]) for g in self.repo.shipment_groups("항공-1")],
            [(1, 2), (3, 2)],
        )
        self.assertEqual(self.repo.next_box_number("해상-1"), 3)

    def test_items_are_not_moved_to_another_shipment_without_asking(self):
        from PySide6.QtWidgets import QMessageBox

        self.window.shipment_input.setText("항공-1")
        self.app.processEvents()
        self.window._select_country("US")
        self.window.fnsku_input.setText("X1")
        self.window.lookup_product()
        self.window.qty_input.setValue(2)
        self.window.add_current_item()
        self.assertEqual(self.window.items_shipment, "항공-1")

        self.window.shipment_input.setText("해상-9")
        self.app.processEvents()
        self.assertIn("항공-1", self.window.next_action.text())

        self.window.box_count.setValue(1)
        for widget in (self.window.weight, self.window.length,
                self.window.width, self.window.height):
            widget.setValue(3)
        with patch("beyondpack.ui.QMessageBox.question", lambda *a, **k: QMessageBox.No):
            self.window.confirm_box_group()
        self.assertEqual(self.repo.shipment_groups("해상-9"), [])
        self.assertEqual(len(self.window.items), 1, "거절했으면 구성품은 그대로 남는다")

        self.window.shipment_input.setText("항공-1")
        self.app.processEvents()
        with patch("beyondpack.ui.QMessageBox.question", lambda *a, **k: QMessageBox.Yes):
            self.window.confirm_box_group()
        self.assertEqual(len(self.repo.shipment_groups("항공-1")), 1)
        self.assertEqual(self.repo.shipment_groups("해상-9"), [])
