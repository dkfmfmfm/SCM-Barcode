"""Shared, word-aware typography for Korean workstation screens."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QApplication, QLabel, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem,
)


def word_lines(text: str, metrics: QFontMetrics, width: int) -> list[str]:
    """Keep eojeol intact; split only a token wider than the entire column."""
    width = max(1, width)
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in re.findall(r"\S+", paragraph):
            candidate = f"{line} {word}" if line else word
            if metrics.horizontalAdvance(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for char in word:
                if line and metrics.horizontalAdvance(line + char) > width:
                    lines.append(line)
                    line = ""
                line += char
        lines.append(line)
    return lines or [""]


class WordLabel(QLabel):
    """A selectable label that wraps Korean at spaces without inflating a window."""

    def __init__(self, text="", parent=None):
        self._source = text
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setText(text)

    def text(self):
        return self._source

    def setText(self, text):
        self._source = str(text)
        self.setToolTip(self._source)
        self._reflow()
        self.updateGeometry()

    def clear(self):
        self.setText("")

    def _available_width(self, width):
        margins = self.contentsMargins()
        return max(1, width - margins.left() - margins.right() - 2 * self.margin())

    def _reflow(self):
        QLabel.setText(self, "\n".join(word_lines(self._source, self.fontMetrics(),
            self._available_width(self.width()))))
        # Qt's preferred height is otherwise calculated at the old, narrow
        # sizeHint. Follow the actual allocated width as the splitter moves.
        required = self.heightForWidth(self.width())
        if self.minimumHeight() != required or self.maximumHeight() != required:
            self.setFixedHeight(required)

    def heightForWidth(self, width):
        margins = self.contentsMargins()
        return (len(word_lines(self._source, self.fontMetrics(), self._available_width(width)))
            * self.fontMetrics().lineSpacing() + margins.top() + margins.bottom() + 2 * self.margin() + 4)

    def sizeHint(self):
        return QSize(280, self.heightForWidth(max(280, self.width())))

    def minimumSizeHint(self):
        return QSize(40, self.fontMetrics().lineSpacing() + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._reflow()
            self.updateGeometry()


class WordDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setFont(opt.font)
        role = QPalette.HighlightedText if opt.state & QStyle.State_Selected else QPalette.Text
        painter.setPen(opt.palette.color(role))
        rect = option.rect.adjusted(10, 6, -10, -6)
        lines = word_lines(text, opt.fontMetrics, rect.width())
        painter.setClipRect(option.rect)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, "\n".join(lines))
        painter.restore()

    def sizeHint(self, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        width = self.parent().columnWidth(index.column())
        return QSize(width, max(38, len(word_lines(opt.text, opt.fontMetrics, width - 20))
            * opt.fontMetrics.lineSpacing() + 14))


def readable_table(table, word_columns=()):
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)
    for column in word_columns:
        table.setItemDelegateForColumn(column, WordDelegate(table))
    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.setInterval(30)
    timer.timeout.connect(table.resizeRowsToContents)
    table.horizontalHeader().sectionResized.connect(lambda *_: timer.start())
    table._word_resize_timer = timer


def stylesheet() -> str:
    return """
    QMainWindow, QDialog { background:#F3F5F7; }
    QWidget { color:#20303D; font-family:'Malgun Gothic','Noto Sans CJK KR','DejaVu Sans'; font-size:13px; }
    QLabel { background:transparent; }
    QLabel#brandTitle { font-size:23px; font-weight:700; letter-spacing:1px; color:#183D45; }
    QLabel#subtitle, QLabel#fieldCaption { color:#647581; font-size:12px; }
    QLabel#sectionTitle { font-size:20px; font-weight:700; }
    QFrame#contextCard { background:white; border:1px solid #DDE4E8; border-radius:12px; }
    QGroupBox { background:white; border:1px solid #DDE4E8; border-radius:12px; margin-top:12px; padding:12px; font-weight:700; }
    QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 6px; color:#36545B; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {
        background:white; border:1px solid #C9D4DB; border-radius:7px; padding:7px 9px; font-size:14px;
        selection-background-color:#D2E8E6; selection-color:#163D42;
    }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus { border:1px solid #16756E; background:#FBFEFD; }
    QLineEdit#scanInput { font-size:20px; font-weight:600; background:#F6FAFA; border:1px solid #8DB8B3; }
    QLineEdit#shipmentInput { font-weight:600; }
    QLineEdit#readonlyField { background:#F4F7F9; border-color:#E7EDF0; color:#334F5B; font-size:13px; }
    QLabel#productName { background:#F4F7F9; border:1px solid #E7EDF0; border-radius:8px; padding:8px 12px; font-size:17px; font-weight:600; }
    QWidget#numericStepper { background:transparent; }
    QPushButton { background:white; border:1px solid #CCD7DC; border-radius:7px; padding:8px 12px; font-weight:600; }
    QPushButton:hover { background:#EDF5F4; border-color:#8BAFA9; }
    QPushButton:pressed { background:#DDECE9; }
    QPushButton:focus { border:2px solid #16756E; }
    QPushButton#primaryButton { background:#EAF3F1; border-color:#AFCAC3; color:#1F5E55; }
    QPushButton#confirmButton { background:#175E55; border:1px solid #175E55; color:white; font-size:16px; }
    QPushButton#confirmButton:hover { background:#124B44; }
    QPushButton#dangerButton { color:#A03939; border-color:#DEC9C9; }
    QPushButton:disabled { background:#EEF1F3; color:#9AA7AE; border-color:#E0E6E9; }
    QPushButton[stepperButton="true"] { background:#F5F8F9; border:1px solid #CCD7DC; border-radius:4px; padding:0; font-size:10px; }
    QPushButton[stepperButton="true"]:hover { background:#DCEDE9; border-color:#71A598; }
    QTableWidget, QListWidget, QTextBrowser { background:white; border:1px solid #E0E7EB; border-radius:8px; alternate-background-color:#F7F9FA; selection-background-color:#DFEEEB; selection-color:#153F38; }
    QTableWidget::item { padding:6px 8px; border-bottom:1px solid #EEF2F4; }
    QHeaderView::section { background:#EDF2F4; color:#4E626E; padding:9px 7px; border:0; font-size:12px; font-weight:600; }
    QTabWidget::pane { background:white; border:1px solid #DDE4E8; border-radius:9px; }
    QTabBar::tab { background:#E8EEF0; color:#667A84; border:0; border-radius:6px; padding:9px 16px; margin:0 5px 5px 0; font-weight:600; }
    QTabBar::tab:selected { background:#D9EAE5; color:#174E44; }
    QLabel#progressSummary, QLabel#packingSummary { background:#F0F6F4; color:#285D50; border:1px solid #DBE9E2; border-radius:8px; padding:8px 12px; }
    QLabel#nextAction { background:#EEF4F8; color:#36566A; border:1px solid #D8E4EC; border-radius:9px; padding:8px 12px; }
    QListWidget::item { padding:11px 10px; border-bottom:1px solid #EEF2F4; }
    QListWidget::item:selected { background:#DFEEEB; color:#153F38; }
    QLabel#backupState { font-size:12px; }
    QStatusBar { color:#73858E; background:#EBF0F2; font-size:12px; }
    QMenu { background:white; border:1px solid #D4DFE4; padding:6px; }
    QMenu::item { padding:9px 22px; border-radius:5px; }
    QMenu::item:selected { background:#E6F0EC; color:#175E55; }
    QMenu::separator { height:1px; background:#E5EBEE; margin:5px 8px; }
    QScrollArea { border:0; background:transparent; }
    QToolTip { color:#20303D; background:#FFFFFF; border:1px solid #CBD8DF; padding:6px; }
    """
