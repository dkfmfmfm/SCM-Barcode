from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from .config import LabelSettings
from .errors import LabelPrintError
from .labels import COMPACT_HEIGHT_MM, render_group_label


# QTextDocument는 붙은 페인트 장치가 없을 때 기본 화면 DPI로 글자 크기를 계산한다.
# 문서를 이 기준으로 배치한 뒤 인쇄영역에 맞춰 통째로 확대·축소하므로 프린터
# 해상도가 달라도 라벨에서 차지하는 비율은 같다.
DOCUMENT_DPI = 96.0


def _document_pixels(millimeter: float) -> float:
    return millimeter / 25.4 * DOCUMENT_DPI


def apply_label_page(printer: QPrinter, label: LabelSettings) -> QSizeF:
    """라벨 용지 규격을 프린터에 지정하고 실제로 적용된 크기를 돌려준다.

    Windows 드라이버가 요청한 사용자 정의 용지를 갖고 있지 않으면 지정이 조용히
    실패하고 기본값(대개 A4)이 유지된다. 그 상태로 인쇄하면 라벨 내용이 잘리고
    남은 A4 높이만큼 빈 라벨이 계속 배출되므로, 호출자가 결과를 확인할 수 있도록
    적용된 크기를 반환한다.
    """
    requested = QSizeF(label.width_mm, label.height_mm)
    margin = max(0.0, label.margin_mm)
    margins = QMarginsF(margin, margin, margin, margin)
    for match in (QPageSize.ExactMatch, QPageSize.FuzzyMatch):
        page_size = QPageSize(requested, QPageSize.Millimeter, "BeyondPackLabel", match)
        layout = QPageLayout(
            page_size, QPageLayout.Portrait, margins, QPageLayout.Millimeter
        )
        if printer.setPageLayout(layout):
            break
        # 일부 드라이버는 용지와 여백을 한 번에 바꾸는 요청만 거부한다.
        if printer.setPageSize(page_size):
            printer.setPageMargins(margins, QPageLayout.Millimeter)
            break
    printer.setFullPage(False)
    return printer.pageLayout().pageSize().size(QPageSize.Millimeter)


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
    printer: QPrinter,
    group: dict,
    items: list[dict],
    numbers: Sequence[int],
    label: LabelSettings,
) -> None:
    """박스번호마다 라벨 1장을 하나의 인쇄 작업으로 출력한다.

    박스마다 인쇄를 따로 호출하면 프린터 드라이버가 각각을 별개 작업으로
    처리해 박스번호가 이어지지 않는다. 한 작업 안에서 페이지를 넘긴다.
    """
    if not numbers:
        raise LabelPrintError("출력할 박스번호가 없습니다.")
    applied = apply_label_page(printer, label)
    if not label.matches_page(applied.width(), applied.height()):
        raise LabelPrintError(
            f"라벨 프린터가 {label.width_mm:g}×{label.height_mm:g}mm 용지를 받아들이지 않아 "
            f"{applied.width():.0f}×{applied.height():.0f}mm로 인쇄됩니다. "
            "이대로 출력하면 라벨이 잘리고 빈 라벨이 함께 배출되므로 중단했습니다. "
            "Windows [설정 > 프린터 및 스캐너 > 해당 프린터 > 인쇄 기본 설정]에서 "
            f"{label.width_mm:g}×{label.height_mm:g}mm 사용자 정의 용지를 등록한 뒤 다시 출력하세요."
        )
    painter = QPainter()
    if not painter.begin(printer):
        raise LabelPrintError(
            "라벨 프린터를 열 수 없습니다. 프린터 연결과 라벨 설정을 확인하세요."
        )
    try:
        document = QTextDocument()
        total = len(numbers)
        # 배치는 설정한 라벨 크기로 결정한다. 프린터가 보고하는 인쇄영역으로
        # 판단하면 용지 지정이 밀렸을 때 표 배치가 선택되어 증상이 겹친다.
        compact = label.height_mm < COMPACT_HEIGHT_MM
        for index, number in enumerate(numbers):
            if index and not printer.newPage():
                raise LabelPrintError("라벨 다음 장으로 넘기지 못했습니다.")
            document.setHtml(render_group_label(group, items, number, total, compact))
            draw_label(printer, painter, document)
    finally:
        painter.end()
