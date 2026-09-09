"""Offline, searchable user guide, bundled with every executable."""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QTextBlockFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from . import __version__
from .presentation import WordLabel, stylesheet


def manual_text() -> str:
    bundled = resources.files("beyondpack").joinpath("resources/docs/USER_MANUAL.md")
    if bundled.is_file():
        return bundled.read_text(encoding="utf-8")
    # Source checkouts keep one canonical manual; PyInstaller bundles that file.
    return (Path(__file__).resolve().parents[2] / "docs/USER_MANUAL.md").read_text(encoding="utf-8")


def guide_sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^## ", text)
    return [(part.split("\n", 1)[0].strip(), "## " + part.strip()) for part in parts[1:]]


class GuideBrowser(QTextBrowser):
    def copy(self):
        # Invisible word joiners affect layout only, never copied instructions.
        selected = self.textCursor().selectedText().replace("\u2060", "").replace("\u2029", "\n")
        QApplication.clipboard().setText(selected)


class HelpDialog(QDialog):
    def __init__(self, parent=None, topic=""):
        super().__init__(parent)
        self.setWindowTitle(f"BeyondPack {__version__} · 사용 안내")
        self.resize(1040, 720)
        self.setMinimumSize(720, 480)
        self.setStyleSheet(stylesheet())
        self.sections = guide_sections(manual_text())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel("작업에 필요한 안내를 바로 찾으세요")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("검색 · 예: 라벨, 정정, 백업, 사용중지")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_topics)
        self.search.returnPressed.connect(self.select_first)
        layout.addWidget(self.search)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.topics = QListWidget()
        self.topics.setMinimumWidth(180)
        self.topics.setWordWrap(True)
        self.topics.currentItemChanged.connect(self.show_topic)
        self.browser = GuideBrowser()
        self.browser.setStyleSheet("QTextBrowser { font-size:15px; }")
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.follow_link)
        self.browser.setReadOnly(True)
        self.browser.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        split.addWidget(self.topics)
        split.addWidget(self.browser)
        split.setSizes([230, 750])
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.result_label = WordLabel("인터넷 없이 이용할 수 있습니다. Ctrl+F로 검색하세요.")
        self.result_label.setObjectName("fieldCaption")
        footer.addWidget(self.result_label, 1)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        find = QAction(self)
        find.setShortcut(QKeySequence.Find)
        find.triggered.connect(self.search.setFocus)
        self.addAction(find)
        copy = QAction(self.browser)
        copy.setShortcut(QKeySequence.Copy)
        copy.setShortcutContext(Qt.WidgetShortcut)
        copy.triggered.connect(self.browser.copy)
        self.browser.addAction(copy)
        self.filter_topics("")
        if topic:
            for i, (title, _) in enumerate(self.sections):
                if topic in title:
                    self.topics.setCurrentRow(i)
                    break

    def filter_topics(self, text):
        self.topics.blockSignals(True)
        self.topics.clear()
        terms = text.casefold().split()
        for index, (title, content) in enumerate(self.sections):
            if all(term in content.casefold() for term in terms):
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, index)
                item.setToolTip(title)
                self.topics.addItem(item)
        self.topics.blockSignals(False)
        if self.topics.count():
            self.topics.setCurrentRow(0)
            self.result_label.setText(f"{self.topics.count()}개 안내 · 인터넷 없이 이용할 수 있습니다.")
        else:
            self.browser.setPlainText("일치하는 안내가 없습니다.\n\n검색어를 짧게 바꾸거나 검색칸을 비워 주세요.")
            self.result_label.setText("검색 결과 없음")

    def select_first(self):
        if self.topics.count():
            self.topics.setCurrentRow(0)
            self.browser.setFocus()

    def show_topic(self, item, _previous=None):
        if not item:
            return
        _, content = self.sections[item.data(Qt.UserRole)]
        document = self.browser.document()
        document.setDefaultStyleSheet("h2 {color:#174E44;} h3 {color:#36545B;} "
            "p,li {line-height:155%;} th {background:#EDF2F4;} td,th {padding:8px;} "
            "code {background:#F0F4F6;}")
        self.browser.setMarkdown(content)
        self.browser.document().setDocumentMargin(22)
        # Qt otherwise permits a break between any pair of Korean syllables.
        plain = document.toPlainText()
        for match in reversed(list(re.finditer(r"[가-힣]{2,}", plain))):
            cursor = QTextCursor(document)
            start = len(plain[:match.start()].encode("utf-16-le")) // 2
            cursor.setPosition(start)
            cursor.setPosition(start + len(match.group()), QTextCursor.KeepAnchor)
            cursor.insertText("\u2060".join(match.group()))
        block = document.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            formatting = block.blockFormat()
            formatting.setLineHeight(140.0, QTextBlockFormat.ProportionalHeight.value)
            formatting.setTopMargin(12 if formatting.headingLevel() else 2)
            formatting.setBottomMargin(8 if formatting.headingLevel() else 5)
            cursor.setBlockFormat(formatting)
            block = block.next()
        self.browser.moveCursor(QTextCursor.Start)
        self.browser.verticalScrollBar().setValue(0)

    def follow_link(self, url):
        if url.fragment():
            self.browser.scrollToAnchor(url.fragment())
