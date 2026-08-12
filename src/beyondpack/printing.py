from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from .config import LabelSettings
from .errors import LabelPrintError
from .labels import render_group_label


# QTextDocument는 붙은 페인트 장치가 없을 때 기본 화면 DPI로 글자 크기를 계산한다.
# 문서를 이 기준으로 배치한 뒤 인쇄영역에 맞춰 통째로 확대·축소하므로 프린터
# 해상도가 달라도 라벨에서 차지하는 비율은 같다.
DOCUMENT_DPI = 96.0


def _document_pixels(millimeter: float) -> float:
    return millimeter / 25.4 * DOCUMENT_DPI


def apply_label_page(printer: QPrinter, label: LabelSettings) -> None:
    """라벨 용지 규격을 프린터에 지정한다.

    지정하지 않으면 드라이버 기본값(대개 A4)으로 전송되어 라벨 내용이 잘리고
    남은 A4 높이만큼 빈 라벨이 계속 배출된다.
    """
    page_size = QPageSize(
        QSizeF(label.width_mm, label.height_mm),
        QPageSize.Millimeter,
        "BeyondPackLabel",
        QPageSize.ExactMatch,
    )
    margin = max(0.0, label.margin_mm)
    printer.setPageLayout(
        QPageLayout(
            page_size,
            QPageLayout.Portrait,
            QMarginsF(margin, margin, margin, margin),
            QPageLayout.Millimeter,
        )
    )
    printer.setFullPage(False)


def draw_label(printer: QPrinter, painter: QPainter, document: QTextDocument) -> None:
    """라벨 1장을 인쇄영역 안에 맞춰 그린다.

    내용이 라벨보다 길면 통째로 축소한다. 넘치는 부분이 다음 장으로 흘러가면
    박스 1개에 라벨이 2장 나오므로 페이지 넘김은 호출자가 전담한다.
    """
    printable = printer.pageLayout().paintRect(QPageLayout.Millimeter)
    device = printer.pageLayout().paintRectPixels(printer.resolution())
    width = _document_pixels(printable.width())
    height = _document_pixels(printable.height())
    document.setDocumentMargin(0)
    document.setTextWidth(width)
    document.setPageSize(QSizeF(width, height))
    used = max(document.size().height(), height)
    scale = min(device.width() / width, device.height() / used)
    painter.save()
    painter.setClipRect(QRectF(0, 0, device.width(), device.height()))
    painter.scale(scale, scale)
    document.drawContents(painter)
    painter.restore()


def print_box_labels(
    printer: QPrinter, group: dict, items: list[dict], numbers: Sequence[int]
) -> None:
    """박스번호마다 라벨 1장을 하나의 인쇄 작업으로 출력한다.

    박스마다 인쇄를 따로 호출하면 프린터 드라이버가 각각을 별개 작업으로
    처리해 박스번호가 이어지지 않는다. 한 작업 안에서 페이지를 넘긴다.
    """
    if not numbers:
        raise LabelPrintError("출력할 박스번호가 없습니다.")
    painter = QPainter()
    if not painter.begin(printer):
        raise LabelPrintError(
            "라벨 프린터를 열 수 없습니다. 프린터 연결과 라벨 설정을 확인하세요."
        )
    try:
        document = QTextDocument()
        total = len(numbers)
        for index, number in enumerate(numbers):
            if index and not printer.newPage():
                raise LabelPrintError("라벨 다음 장으로 넘기지 못했습니다.")
            document.setHtml(render_group_label(group, items, number, total))
            draw_label(printer, painter, document)
    finally:
        painter.end()
