from __future__ import annotations

import html
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QPageSize
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .backup import BackupResult, BackupRunner, restore_packaging_backup, station_name
from .cache import ProductCacheRepository
from .config import AppConfig, BackupSettings, LabelSettings, save_config
from .diagnostics import create_diagnostic_bundle
from .errors import BeyondPackError, PackagingValidationError
from .exporter import export_shipment_xlsx
from .history import JobHistoryDialog
from .help import HelpDialog
from .presentation import WordLabel, readable_table, stylesheet
from .labels import box_numbers
from .models import BoxGroupInput, BoxItem, Product, utc_now_iso
from .normalization import normalize_shipment_code, positive_decimal, positive_int
from .packaging import PackagingRepository
from .printing import apply_label_page, print_box_labels
from .sources.base import ProductSource
from .sources.excel_source import ExcelProductSource
from .sources.google_sheets import google_sheet_csv_url
from .sync import ProductSyncService, SyncResult


COLORS = {
    "CURRENT": ("#E9F7EF", "#166534", "최신"),
    "CACHED": ("#FFF7E6", "#8A6425", "저장된 상품정보"),
    "SYNCING": ("#EAF2FF", "#1D4ED8", "업데이트 중"),
    "ERROR": ("#FDECEC", "#B91C1C", "오류"),
    "NO_DATA": ("#FDECEC", "#B91C1C", "상품DB 없음"),
}


class SyncWorker(QObject):
    finished = Signal(object)
    login_required = Signal(str)

    def __init__(
        self,
        source_factory: Callable[[Callable[[str], None]], ProductSource],
        cache: ProductCacheRepository,
        status_path: Path,
        drop_threshold: float,
    ):
        super().__init__()
        self.source_factory = source_factory
        self.cache = cache
        self.status_path = status_path
        self.drop_threshold = drop_threshold

    @Slot()
    def run(self) -> None:
        try:
            source = self.source_factory(self.login_required.emit)
            result = ProductSyncService(
                source, self.cache, self.status_path, self.drop_threshold
            ).sync()
        except Exception as exc:
            info = self.cache.info()
            result = SyncResult(
                "CACHED" if info.product_count else "NO_DATA",
                f"상품정보 업데이트를 시작할 수 없습니다. ({getattr(exc, 'code', 'BP-SYNC-000')}: {exc})",
                info,
                "",
            )
        self.finished.emit(result)


class BackupWorker(QObject):
    """백업은 공유 폴더가 느리거나 끊겨도 포장 작업을 멈추면 안 된다."""

    finished = Signal(object)

    def __init__(self, backup: BackupRunner):
        super().__init__()
        self.backup = backup

    @Slot()
    def run(self) -> None:
        try:
            result = self.backup.run()
        except Exception as exc:  # 백업 실패가 작업을 막지 않게 전부 흡수한다.
            result = BackupResult(False, f"백업 실패: {exc}", utc_now_iso())
        self.finished.emit(result)


class BackupSettingsDialog(QDialog):
    """포장 실적을 작업 PC 밖에 자동 복사할 위치를 지정한다."""

    def __init__(self, settings: BackupSettings, station: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("자동 백업 설정")
        self.setMinimumWidth(520)

        self.enabled = QCheckBox("포장 실적과 상품 마스터를 지정한 위치에 자동으로 복사한다")
        self.enabled.setChecked(settings.enabled)

        self.directory_input = QLineEdit(settings.directory)
        self.directory_input.setPlaceholderText(r"예: \\서버\물류\BeyondPack 또는 D:\백업")
        self.directory_input.setMinimumHeight(36)
        browse = QPushButton("폴더 선택")
        browse.clicked.connect(self._choose_directory)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory_input, 1)
        directory_row.addWidget(browse)

        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 240)
        self.interval_input.setSuffix(" 분마다")
        self.interval_input.setMinimumHeight(36)
        self.interval_input.setValue(settings.interval_minutes)

        self.keep_input = QSpinBox()
        self.keep_input.setRange(1, 3650)
        self.keep_input.setSuffix(" 일 보관 (일자별 CSV·상품 마스터)")
        self.keep_input.setMinimumHeight(36)
        self.keep_input.setValue(settings.keep_days)

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("백업 위치", directory_row)
        form.addRow("백업 주기", self.interval_input)
        form.addRow("사본 보관", self.keep_input)

        guide = WordLabel(
            f"이 PC의 포장기록은 {station} 폴더에 보관합니다. "
            "상품 마스터는 products 폴더에 Excel로 저장합니다.\n"
            "작업 후·설정 주기·정상 종료 시 자동으로 백업합니다. "
            "외부 연결이 끊겨도 로컬 백업과 포장 작업은 유지됩니다."
        )
        guide.setObjectName("fieldCaption")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(guide)
        layout.addWidget(buttons)

    @Slot()
    def _choose_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "백업 위치 선택", self.directory_input.text() or str(Path.home())
        )
        if chosen:
            self.directory_input.setText(chosen)

    def settings(self) -> BackupSettings:
        return BackupSettings(
            directory=self.directory_input.text().strip(),
            enabled=self.enabled.isChecked(),
            interval_minutes=self.interval_input.value(),
            keep_days=self.keep_input.value(),
        )


class LabelSettingsDialog(QDialog):
    """라벨 프린터와 용지 규격을 코드 수정 없이 지정한다."""

    NO_PRINTER = "인쇄할 때 선택"

    def __init__(self, settings: LabelSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("라벨 설정")
        self.setMinimumWidth(430)

        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumHeight(36)
        self.printer_combo.addItem(self.NO_PRINTER, "")
        for name in QPrinterInfo.availablePrinterNames():
            self.printer_combo.addItem(name, name)
        index = self.printer_combo.findData(settings.printer_name.strip())
        if index < 0 and settings.printer_name.strip():
            self.printer_combo.addItem(
                f"{settings.printer_name} (연결 안 됨)", settings.printer_name
            )
            index = self.printer_combo.count() - 1
        self.printer_combo.setCurrentIndex(max(0, index))

        self.width_input = self._millimeter_box(settings.width_mm, 20.0, 300.0)
        self.height_input = self._millimeter_box(settings.height_mm, 15.0, 300.0)
        self.margin_input = self._millimeter_box(settings.margin_mm, 0.0, 20.0)
        self.auto_print = QCheckBox("박스 확정과 동시에 라벨을 자동 출력한다")
        self.auto_print.setChecked(settings.auto_print)

        form = QFormLayout()
        form.addRow("라벨 프린터", self.printer_combo)
        form.addRow("라벨 가로", self.width_input)
        form.addRow("라벨 세로", self.height_input)
        form.addRow("여백", self.margin_input)
        form.addRow("", self.auto_print)

        guide = WordLabel(
            "라벨 롤의 실제 크기를 mm로 입력하세요. 크기가 맞지 않으면 내용이 잘리거나 "
            "빈 라벨이 함께 배출됩니다. 저장 후 '테스트 라벨 출력'으로 확인하세요."
        )
        guide.setObjectName("fieldCaption")

        self.support_label = WordLabel()
        self.printer_combo.currentIndexChanged.connect(self._refresh_support)
        self.width_input.valueChanged.connect(self._refresh_support)
        self.height_input.valueChanged.connect(self._refresh_support)
        self._refresh_support()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.support_label)
        layout.addWidget(guide)
        layout.addWidget(buttons)

    @Slot()
    def _refresh_support(self) -> None:
        """선택한 프린터에 이 라벨 크기가 등록되어 있는지 미리 알려준다.

        Windows 드라이버에 해당 사용자 정의 용지가 없으면 인쇄 시점에 크기 지정이
        실패하므로, 설정 단계에서 먼저 확인할 수 있게 한다.
        """
        style = "border-radius:6px; padding:9px; font-weight:700;"
        name = str(self.printer_combo.currentData() or "")
        if not name:
            self.support_label.setText("인쇄할 때마다 프린터를 선택합니다.")
            self.support_label.setStyleSheet(
                f"background:#F1EEE8; color:#667085; {style}"
            )
            return
        info = QPrinterInfo.printerInfo(name)
        settings = self.settings()
        if info.isNull():
            self.support_label.setText(
                f"'{name}'를 찾을 수 없습니다. 프린터 연결과 전원을 확인하세요."
            )
            self.support_label.setStyleSheet(
                f"background:#FDECEC; color:#B91C1C; {style}"
            )
            return
        sizes = [
            page.size(QPageSize.Millimeter) for page in info.supportedPageSizes()
        ]
        target = f"{settings.width_mm:g}×{settings.height_mm:g}mm"
        if any(settings.matches_page(size.width(), size.height()) for size in sizes):
            self.support_label.setText(f"이 프린터에 {target} 용지가 등록되어 있습니다.")
            self.support_label.setStyleSheet(
                f"background:#E9F7EF; color:#166534; {style}"
            )
            return
        registered = ", ".join(
            f"{size.width():.0f}×{size.height():.0f}" for size in sizes[:6]
        )
        self.support_label.setText(
            f"이 프린터의 등록된 용지 목록에 {target}가 없습니다"
            + (f" (등록된 크기 mm: {registered})" if registered else "")
            + ". 그대로 인쇄하면 A4로 나가 라벨이 잘리므로, Windows "
            "[설정 > 프린터 및 스캐너 > 인쇄 기본 설정]에서 이 크기를 "
            "사용자 정의 용지로 추가하세요."
        )
        self.support_label.setStyleSheet(f"background:#FFF7E6; color:#9A5B00; {style}")

    @staticmethod
    def _millimeter_box(value: float, minimum: float, maximum: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(1)
        box.setSingleStep(1.0)
        box.setSuffix(" mm")
        box.setMinimumHeight(36)
        box.setValue(value)
        return box

    def settings(self) -> LabelSettings:
        return LabelSettings(
            printer_name=str(self.printer_combo.currentData() or ""),
            width_mm=self.width_input.value(),
            height_mm=self.height_input.value(),
            margin_mm=self.margin_input.value(),
            auto_print=self.auto_print.isChecked(),
        )


class MainWindow(QMainWindow):
    DRAFT_KEY = "current-packaging"

    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        cache: ProductCacheRepository,
        packaging: PackagingRepository,
        source_factory: Callable[[Callable[[str], None]], ProductSource],
        auto_sync: bool = True,
    ):
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.cache = cache
        self.packaging = packaging
        self.source_factory = source_factory
        self.current_product: Product | None = None
        self.items: list[BoxItem] = []
        self.job_id: str | None = None
        self.job_shipment = ""
        self.edit_group_id = ""
        self.edit_reason = ""
        self.edit_box_count = 0
        self._restoring_input = False
        self.last_saved: tuple[dict, list[dict]] | None = None
        self.sync_thread: QThread | None = None
        self.backup_thread: QThread | None = None
        self.station = station_name()
        self.last_backup: BackupResult | None = None
        self.cache_blocked = False

        self.setWindowTitle(f"BeyondPack {__version__} · BEYOND EARTH")
        self.setMinimumSize(760, 520)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1360, screen.width()), min(900, screen.height() - 40))
        self._build_ui()
        self._build_actions()
        self._connect_autosave()
        self._refresh_country_options()
        self._restore_active_job()
        self._restore_draft()
        self._refresh_next_box_label()
        self._refresh_shipment_view()
        # 번호가 밀린 박스를 아직 다시 붙이지 않았다면 재시작해도 안내가 남는다.
        self._refresh_relabel_banner()
        self._show_initial_cache_state()
        self._start_backup_schedule()
        if auto_sync:
            QTimer.singleShot(150, self.sync_now)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("BeyondPack")
        title.setObjectName("brandTitle")
        subtitle = QLabel(f"BEYOND EARTH  /  {__version__}")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.update_button = QPushButton("상품 업데이트  F2")
        self.update_button.clicked.connect(self.sync_now)
        header.addWidget(self.update_button)
        self.sheet_settings_button = QPushButton("Sheet 설정")
        self.sheet_settings_button.clicked.connect(self.configure_google_sheet)
        header.addWidget(self.sheet_settings_button)
        self.help_button = QPushButton("사용 안내  F1")
        self.help_button.setObjectName("primaryButton")
        self.help_button.clicked.connect(self.show_help)
        header.addWidget(self.help_button)
        layout.addLayout(header)

        context = QFrame()
        context.setObjectName("contextCard")
        context_row = QHBoxLayout(context)
        context_row.setContentsMargins(14, 10, 14, 10)
        context_row.setSpacing(16)

        def context_field(label, widget, stretch):
            column = QVBoxLayout()
            column.setSpacing(5)
            caption = QLabel(label)
            caption.setObjectName("fieldCaption")
            caption.setBuddy(widget)
            widget.setAccessibleName(label)
            column.addWidget(caption)
            column.addWidget(widget)
            context_row.addLayout(column, stretch)

        self.country_combo = QComboBox()
        self.country_combo.setMinimumWidth(110)
        self.country_combo.setMinimumHeight(38)
        self.country_combo.setObjectName("countrySelector")
        self.country_combo.currentIndexChanged.connect(self._country_changed)
        context_field("작업 국가", self.country_combo, 1)
        self.operator_input = QLineEdit(self.config.operator_name)
        self.operator_input.setPlaceholderText("이름 또는 사번")
        self.operator_input.setMinimumHeight(38)
        context_field("작업자", self.operator_input, 2)
        self.shipment_input = QLineEdit()
        self.shipment_input.setObjectName("shipmentInput")
        self.shipment_input.setPlaceholderText("문서번호 입력 · 최근 출고건 선택")
        self.shipment_input.setMinimumHeight(38)
        self.shipment_input.setClearButtonEnabled(True)
        self.shipment_completer_model = QStringListModel(self)
        completer = QCompleter(self.shipment_completer_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.shipment_input.setCompleter(completer)
        self.shipment_input.textChanged.connect(self._shipment_changed)
        context_field("출고건", self.shipment_input, 5)
        self.next_box_label = QLabel()
        self.next_box_label.setObjectName("nextBox")
        self.next_box_label.setMinimumWidth(96)
        self.next_box_label.setAlignment(Qt.AlignCenter)
        context_row.addWidget(self.next_box_label)
        layout.addWidget(context)

        self.sync_banner = QFrame()
        self.sync_banner.setObjectName("syncBanner")
        banner_layout = QHBoxLayout(self.sync_banner)
        banner_layout.setContentsMargins(14, 9, 14, 9)
        self.sync_state_label = QLabel()
        self.sync_detail_label = WordLabel()
        self.sync_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        banner_layout.addWidget(self.sync_state_label)
        banner_layout.addWidget(self.sync_detail_label, 1)
        self.backup_label = QLabel()
        self.backup_label.setObjectName("backupState")
        banner_layout.addWidget(self.backup_label)
        layout.addWidget(self.sync_banner)

        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        left_widget, right_widget = QWidget(), QWidget()
        left_widget.setMinimumWidth(520)
        right_widget.setMinimumWidth(330)
        left, right = QVBoxLayout(left_widget), QVBoxLayout(right_widget)
        for column in (left, right):
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(10)
        self.body_splitter.addWidget(left_widget)
        self.body_splitter.addWidget(right_widget)
        self.body_splitter.setSizes([760, 480])
        self.body_splitter.setStretchFactor(0, 6)
        self.body_splitter.setStretchFactor(1, 4)

        lookup_group = QGroupBox("01  상품 확인")
        lookup_layout = QVBoxLayout(lookup_group)
        scan_row = QHBoxLayout()
        self.fnsku_input = QLineEdit()
        self.fnsku_input.setObjectName("scanInput")
        self.fnsku_input.setAccessibleName("FNSKU 스캔")
        self.fnsku_input.setPlaceholderText("FNSKU를 스캔하세요")
        self.fnsku_input.setClearButtonEnabled(True)
        self.fnsku_input.setMinimumHeight(48)
        self.fnsku_input.returnPressed.connect(self.lookup_product)
        scan_button = QPushButton("조회")
        scan_button.setMinimumHeight(48)
        scan_button.clicked.connect(self.lookup_product)
        scan_row.addWidget(self.fnsku_input, 1)
        scan_row.addWidget(scan_button)
        lookup_layout.addLayout(scan_row)

        product_grid = QGridLayout()
        self.product_fields: dict[str, QLineEdit | WordLabel] = {}
        specs = [
            ("product_name", "품목명", 0, 0, 1, 4),
            ("item_code", "품목코드", 1, 0, 1, 1),
            ("sku", "SKU", 1, 1, 1, 1),
            ("fnsku", "FNSKU", 1, 2, 1, 1),
            ("country_name", "국가", 1, 3, 1, 1),
        ]
        for key, label, row, column, row_span, col_span in specs:
            box = QVBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("fieldCaption")
            if key == "product_name":
                value = WordLabel()
                value.setObjectName("productName")
                value.setContentsMargins(12, 8, 12, 8)
            else:
                value = QLineEdit()
                value.setReadOnly(True)
                value.setObjectName("readonlyField")
                value.setMinimumWidth(50)
            value.setAccessibleName(label)
            value.setMinimumHeight(38)
            self.product_fields[key] = value
            box.addWidget(caption)
            box.addWidget(value)
            product_grid.addLayout(box, row, column, row_span, col_span)
        lookup_layout.addLayout(product_grid)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("박스당 수량"))
        self.qty_input = QSpinBox()
        self.qty_input.setObjectName("qtyInput")
        self.qty_input.setRange(1, 999999)
        self.qty_input.setSingleStep(1)
        self.qty_input.setValue(1)
        self.qty_input.setSuffix(" EA")
        self.qty_input.lineEdit().installEventFilter(self)
        add_row.addWidget(self._stepper(self.qty_input, "qty", "박스당 상품수량"))
        self.add_item_button = QPushButton("구성품 추가")
        self.add_item_button.setToolTip("수량 입력 후 Enter · Alt+A")
        self.add_item_button.setObjectName("primaryButton")
        self.add_item_button.setMinimumHeight(38)
        self.add_item_button.clicked.connect(self.add_current_item)
        add_row.addWidget(self.add_item_button, 1)
        lookup_layout.addLayout(add_row)
        lookup_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        left.addWidget(lookup_group, 0)

        # 구성품과 출고건 현황을 탭으로 묶는다. 두 표를 세로로 쌓으면 1120x760
        # 최소 창에서 위쪽 입력 영역이 눌려 겹친다.
        self.work_tabs = QTabWidget()
        items_page = QWidget()
        items_layout = QVBoxLayout(items_page)
        items_layout.setContentsMargins(0, 10, 0, 0)
        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels(
            ["품목코드", "FNSKU", "국가", "품목명", "EA/BOX"]
        )
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        header_view = self.items_table.horizontalHeader()
        for col in range(5):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.Stretch)
        self.items_table.setMinimumHeight(84)
        readable_table(self.items_table, (3,))
        items_layout.addWidget(self.items_table)
        item_actions = QHBoxLayout()
        self.items_hint = WordLabel("상품을 조회하고 박스당 수량을 입력하세요.")
        self.items_hint.setObjectName("fieldCaption")
        item_actions.addWidget(self.items_hint, 1)
        self.edit_qty_button = QPushButton("수량 변경")
        self.edit_qty_button.setEnabled(False)
        self.edit_qty_button.clicked.connect(self.edit_item_quantity)
        self.items_table.cellDoubleClicked.connect(lambda *_: self.edit_item_quantity())
        remove_button = QPushButton("선택 상품 제거")
        remove_button.clicked.connect(self.remove_selected_item)
        self.remove_item_button = remove_button
        remove_button.setEnabled(False)
        self.items_table.itemSelectionChanged.connect(self._item_selection_changed)
        item_actions.addWidget(self.edit_qty_button)
        item_actions.addWidget(remove_button)
        items_layout.addLayout(item_actions)
        self.work_tabs.addTab(items_page, "02  박스 구성품")

        progress_page = QWidget()
        progress_layout = QVBoxLayout(progress_page)
        progress_layout.setContentsMargins(0, 10, 0, 0)
        self.progress_summary = WordLabel()
        self.progress_summary.setObjectName("progressSummary")
        self.progress_summary.setContentsMargins(10, 8, 10, 8)
        progress_layout.addWidget(self.progress_summary)
        self.progress_table = QTableWidget(0, 6)
        self.progress_table.setHorizontalHeaderLabels(
            ["박스번호", "FNSKU", "EA/BOX", "무게", "규격(가로×세로×높이)", "확정시각"]
        )
        self.progress_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.progress_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.progress_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.progress_table.verticalHeader().setVisible(False)
        self.progress_table.setAlternatingRowColors(True)
        self.progress_table.setMinimumHeight(104)
        readable_table(self.progress_table)
        progress_header = self.progress_table.horizontalHeader()
        for column in range(6):
            progress_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        progress_header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.progress_table.itemSelectionChanged.connect(self._progress_selection_changed)
        progress_layout.addWidget(self.progress_table)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.reprint_selected_button = QPushButton("라벨 재출력")
        self.reprint_selected_button.setEnabled(False)
        self.reprint_selected_button.clicked.connect(self.print_selected_group)
        self.amend_selected_button = QPushButton("정정")
        self.amend_selected_button.setEnabled(False)
        self.amend_selected_button.clicked.connect(self.amend_selected_group)
        self.delete_selected_button = QPushButton("확정 취소")
        self.delete_selected_button.setObjectName("dangerButton")
        self.delete_selected_button.setEnabled(False)
        self.delete_selected_button.clicked.connect(self.delete_selected_group)
        for button in (
            self.reprint_selected_button,
            self.amend_selected_button,
            self.delete_selected_button,
        ):
            action_row.addWidget(button)
        progress_layout.addLayout(action_row)
        self.work_tabs.addTab(progress_page, "출고 현황")
        # 재부착 안내는 탭 위에 둔다. 구성품 탭에서 작업하는 동안에도 보여야
        # 번호가 밀린 상자를 다시 붙이는 일을 잊지 않는다.
        self.relabel_banner = WordLabel()
        self.relabel_banner.setObjectName("relabelBanner")
        self.relabel_banner.setContentsMargins(10, 8, 10, 8)
        self.relabel_banner.setStyleSheet(
            "background:#FFF4E5; color:#8A4B00; border:1px solid #F0C48A;"
            "border-radius:6px; font-weight:700;"
        )
        self.relabel_banner.setVisible(False)
        self.relabel_print_button = QPushButton("재부착 라벨 출력")
        self.relabel_print_button.setVisible(False)
        self.relabel_print_button.clicked.connect(self.print_relabel_batch)
        self.relabel_done_button = QPushButton("재부착 완료")
        self.relabel_done_button.setVisible(False)
        self.relabel_done_button.clicked.connect(self.finish_relabel)
        relabel_row = QHBoxLayout()
        relabel_row.addWidget(self.relabel_print_button)
        relabel_row.addWidget(self.relabel_done_button)
        relabel_row.addStretch()
        left.addWidget(self.relabel_banner)
        left.addLayout(relabel_row)
        left.addWidget(self.work_tabs, 1)

        package_group = QGroupBox("03  포장 규격")
        form = QFormLayout(package_group)
        form.setVerticalSpacing(8)
        self.box_count = QSpinBox()
        self.box_count.setObjectName("boxCountInput")
        # 미입력을 빈칸으로 보여 준다. 기본값 1은 그 자체가 올바른 값이어서
        # 작업자가 박스수량을 넣었는지 화면으로 구분할 수 없었다.
        # 빈 문자열은 Qt에서 "특수값 표시 안 함"으로 해석되므로 공백 한 칸을 쓴다.
        self.box_count.setRange(0, 99999)
        self.box_count.setSpecialValueText(" ")
        self.box_count.setSingleStep(1)
        self.box_count.setSuffix(" BOX")
        self.weight = self._decimal_box("weightInput", " kg / BOX", 3, self.config.weight_max_kg)
        self.length = self._decimal_box("lengthInput", " cm", 2, self.config.dimension_max_cm)
        self.width = self._decimal_box("widthInput", " cm", 2, self.config.dimension_max_cm)
        self.height = self._decimal_box("heightInput", " cm", 2, self.config.dimension_max_cm)
        form.addRow("박스수량", self._stepper(self.box_count, "boxCount", "박스수량"))
        form.addRow("무게", self._stepper(self.weight, "weight", "무게"))
        form.addRow("가로", self._stepper(self.length, "length", "가로"))
        form.addRow("세로", self._stepper(self.width, "width", "세로"))
        form.addRow("높이", self._stepper(self.height, "height", "높이"))
        right.addWidget(package_group)

        self.packing_summary = WordLabel("구성품과 포장 규격을 입력하면 합계를 확인할 수 있습니다.")
        self.packing_summary.setObjectName("packingSummary")
        self.packing_summary.setContentsMargins(12, 9, 12, 9)
        for widget in (self.box_count, self.weight, self.length, self.width, self.height):
            widget.valueChanged.connect(self._refresh_packing_summary)

        self.confirm_button = QPushButton("박스 확정   Ctrl+Enter")
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.setMinimumHeight(54)
        self.confirm_button.clicked.connect(self.confirm_box_group)

        utility_group = QGroupBox("작업 도구")
        utility_layout = QGridLayout(utility_group)
        reset_button = QPushButton("입력 초기화  F4")
        reset_button.clicked.connect(self.reset_current)
        print_button = QPushButton("마지막 라벨  F8")
        print_button.clicked.connect(self.print_last_labels)
        export_button = QPushButton("출고건 Excel")
        export_button.clicked.connect(self.export_current_job)
        # 설정·비상 업데이트·진단은 작업 중에 쓰지 않는다. 한 버튼에 모아
        # 오작동을 줄이고 작업 화면의 세로 공간을 비운다.
        admin_button = QPushButton("설정·관리  ▾")
        admin_menu = QMenu(admin_button)
        for text, callback in (
            ("자동 백업 설정", self.configure_backup),
            ("지금 백업", self.backup_now),
            ("라벨 설정", self.configure_labels),
            ("테스트 라벨 출력", self.print_test_label),
            ("Excel 비상 업데이트", self.import_excel_products),
            ("관리자 진단파일 생성", self.create_diagnostics),
            ("포장 백업 복원", self.restore_backup),
            ("새 작업 시작", self.new_job),
            ("구성품 정보 재확인", self.refresh_current_products),
        ):
            admin_menu.addAction(text, callback)
        self.excel_import_action = admin_menu.actions()[4]
        admin_button.setMenu(admin_menu)
        utility_layout.addWidget(reset_button, 0, 0)
        utility_layout.addWidget(print_button, 0, 1)
        utility_layout.addWidget(export_button, 1, 0)
        utility_layout.addWidget(admin_button, 1, 1)
        history_button = QPushButton("이전 작업")
        history_button.setToolTip("작업 검색 · 이어하기 · 취소·정정 이력")
        history_button.clicked.connect(self.show_history)
        finish_button = QPushButton("작업 완료")
        finish_button.clicked.connect(self.finish_job)
        utility_layout.addWidget(history_button, 2, 0)
        utility_layout.addWidget(finish_button, 2, 1)
        right.addWidget(utility_group)
        right.addStretch()

        self.next_action = WordLabel("FNSKU를 스캔하세요.")
        self.next_action.setObjectName("nextAction")
        self.next_action.setContentsMargins(12, 10, 12, 10)
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setWidget(self.body_splitter)
        layout.addWidget(self.body_scroll, 1)
        confirm_row = QHBoxLayout()
        confirm_row.addWidget(self.packing_summary, 1)
        self.confirm_button.setMinimumWidth(310)
        confirm_row.addWidget(self.confirm_button)
        layout.addLayout(confirm_row)
        layout.addWidget(self.next_action)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("준비 · F1 사용 안내 · 수량 입력 후 Enter로 추가")
        self.setStyleSheet(self._stylesheet())
        for widget in (self.country_combo, self.qty_input, self.box_count,
                self.weight, self.length, self.width, self.height):
            widget.installEventFilter(self)
        QWidget.setTabOrder(self.country_combo, self.operator_input)
        QWidget.setTabOrder(self.operator_input, self.shipment_input)
        QWidget.setTabOrder(self.shipment_input, self.fnsku_input)
        QWidget.setTabOrder(self.fnsku_input, self.qty_input)
        QWidget.setTabOrder(self.qty_input, self.add_item_button)
        QWidget.setTabOrder(self.add_item_button, self.box_count)
        for first, second in zip((self.box_count, self.weight, self.length, self.width, self.height),
                (self.weight, self.length, self.width, self.height, self.confirm_button)):
            QWidget.setTabOrder(first, second)

    def _decimal_box(
        self, object_name: str, suffix: str, decimals: int, maximum: float
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setObjectName(object_name)
        box.setRange(0, maximum)
        box.setDecimals(decimals)
        box.setSingleStep(0.1)
        box.setSuffix(suffix)
        return box

    def _stepper(
        self, box: QAbstractSpinBox, key: str, field_name: str
    ) -> QWidget:
        box.setButtonSymbols(QAbstractSpinBox.NoButtons)
        box.setAlignment(Qt.AlignRight)
        box.setMinimumHeight(40)
        box.setMinimumWidth(130)

        wrapper = QWidget()
        wrapper.setObjectName("numericStepper")
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        row.addWidget(box, 1)

        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 0, 0, 0)
        button_column.setSpacing(3)
        up_button = QPushButton("▲")
        down_button = QPushButton("▼")
        for button, action_name in (
            (up_button, "올리기"),
            (down_button, "내리기"),
        ):
            button.setProperty("stepperButton", True)
            button.setMinimumSize(44, 21)
            button.setMaximumHeight(22)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(90)
            button.setFocusPolicy(Qt.NoFocus)
            button.setAccessibleName(f"{field_name} {action_name}")
            button.setToolTip(f"{field_name} {action_name}")
        up_button.setObjectName(f"{key}StepUp")
        down_button.setObjectName(f"{key}StepDown")
        up_button.clicked.connect(box.stepUp)
        down_button.clicked.connect(box.stepDown)
        button_column.addWidget(up_button)
        button_column.addWidget(down_button)
        row.addLayout(button_column)

        if not hasattr(self, "step_buttons"):
            self.step_buttons: dict[str, tuple[QPushButton, QPushButton]] = {}
        self.step_buttons[key] = (up_button, down_button)
        return wrapper

    def _build_actions(self) -> None:
        shortcuts = [
            ("help", "F1", self.show_help),
            ("add", "Alt+A", self.add_current_item),
            ("sync", "F2", self.sync_now),
            ("reset", "F4", self.reset_current),
            ("print", "F8", self.print_last_labels),
            ("confirm", "Ctrl+Return", self.confirm_box_group),
        ]
        for name, key, callback in shortcuts:
            action = QAction(name, self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and not watched.hasFocus():
            event.ignore()
            return True
        if event.type() == QEvent.FocusIn and isinstance(watched, QAbstractSpinBox):
            # 마우스로 누르면 Qt가 누른 자리에 커서를 놓는다. 빈칸이나 0이 남은
            # 칸이면 친 숫자가 기존 글자 **앞에** 끼어들어, 박스수량에 10을 치면
            # 01(=1개)이 되고 무게 0.000에 10을 치면 100.000이 됐다.
            # 커서 배치가 이 이벤트 뒤에 일어나므로 한 박자 늦춰 전체를 선택한다.
            # 이미 포커스가 있는 칸을 다시 누르면 FocusIn이 오지 않으므로,
            # 자릿수 하나만 고치는 조작은 그대로 된다.
            QTimer.singleShot(0, watched, watched.selectAll)
        if (hasattr(self, "qty_input") and watched is self.qty_input.lineEdit()
                and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() == Qt.NoModifier):
            self.qty_input.interpretText()
            self.add_current_item()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "body_splitter"):
            orientation = Qt.Vertical if event.size().width() < 980 else Qt.Horizontal
            if self.body_splitter.orientation() != orientation:
                self.body_splitter.setOrientation(orientation)
                self.body_splitter.setSizes([620, 480])

    def show_help(self):
        try:
            dialog = HelpDialog(self)
        except OSError as exc:
            self._error(f"사용 안내를 열 수 없습니다. 설치파일을 다시 확인하세요. {exc}")
            return
        dialog.exec()
        self._focus_scan_input()

    def _item_selection_changed(self):
        selected = bool(self.items_table.selectedItems())
        self.edit_qty_button.setEnabled(selected)
        self.remove_item_button.setEnabled(selected)

    def edit_item_quantity(self):
        row = self.items_table.currentRow()
        if not 0 <= row < len(self.items):
            return
        item = self.items[row]
        quantity, ok = QInputDialog.getInt(self, "구성품 수량 변경",
            f"{item.product_name}\n박스당 수량", item.qty_per_box, 1, 999999)
        if ok:
            self.items[row] = replace(item, qty_per_box=quantity)
            self._refresh_items_table()
            self._save_draft()
            self._success("구성품 수량을 반영했습니다.")

    def _refresh_packing_summary(self):
        if not hasattr(self, "packing_summary"):
            return
        units = sum(item.qty_per_box for item in self.items)
        boxes = self.box_count.value()
        self.packing_summary.setText(
            f"{len(self.items):,}품목 · 박스당 {units:,}개\n"
            f"{boxes:,}박스 · 총 {units * boxes:,}개 · 총 중량 {self.weight.value() * boxes:,.3f} kg"
        )
        self.items_hint.setText(f"{len(self.items):,}품목 · 박스당 {units:,}개 · 더블클릭으로 수량 변경"
            if self.items else "상품을 조회하고 박스당 수량을 입력하세요.")

    def _focus_first_incomplete(self):
        fields = [(self.operator_input, bool(self.operator_input.text().strip())),
            (self.shipment_input, bool(self._shipment_code())),
            (self.fnsku_input, bool(self.items))]
        fields.extend((widget, widget.value() > 0) for widget in
            (self.box_count, self.weight, self.length, self.width, self.height))
        for widget, complete in fields:
            if not complete:
                self.body_scroll.ensureWidgetVisible(widget)
                widget.setFocus()
                if hasattr(widget, "selectAll"):
                    widget.selectAll()
                break

    def _connect_autosave(self) -> None:
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(350)
        self.autosave_timer.timeout.connect(self._save_draft)
        for widget in (self.box_count, self.weight, self.length, self.width, self.height):
            widget.valueChanged.connect(lambda _value: self.autosave_timer.start())

    # ---- 포장 실적 자동 백업 -------------------------------------------------

    def _start_backup_schedule(self) -> None:
        """주기 백업과 확정 직후 백업을 건다.

        확정마다 바로 쓰면 공유 폴더가 느릴 때 작업 리듬을 해치므로, 마지막
        확정에서 잠시 쉰 뒤 한 번만 실행한다.
        """
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(lambda: self.run_backup("주기"))
        self.backup_after_confirm = QTimer(self)
        self.backup_after_confirm.setSingleShot(True)
        self.backup_after_confirm.setInterval(20_000)
        self.backup_after_confirm.timeout.connect(lambda: self.run_backup("확정"))
        self._apply_backup_schedule()

    def _apply_backup_schedule(self) -> None:
        settings = self.config.backup
        self.backup_timer.stop()
        self.backup_timer.start(max(1, settings.interval_minutes) * 60_000)
        self._refresh_backup_label()

    def _refresh_backup_label(self) -> None:
        style = "border-radius:6px; padding:4px 9px; font-weight:700;"
        if not self.config.backup.active:
            self.backup_label.setText("로컬 백업 · 외부 미설정" if not self.last_backup else
                ("로컬 백업 완료 · 외부 미설정" if self.last_backup.ok else "로컬 백업 실패"))
            self.backup_label.setToolTip(
                "로컬 자동 백업은 같은 PC에 보관됩니다. PC 고장에 대비하려면 "
                "'설정·관리 > 자동 백업 설정'에서 백업 위치를 지정하세요."
            )
            failed = self.last_backup is not None and not self.last_backup.ok
            self.backup_label.setStyleSheet(
                f"background:{'#FDECEC' if failed else '#FFF7E6'}; "
                f"color:{'#B91C1C' if failed else '#8A6425'}; {style}")
            return
        result = self.last_backup
        if result is None:
            self.backup_label.setText("백업 대기")
            self.backup_label.setToolTip(f"백업 위치: {self.config.backup.directory}")
            self.backup_label.setStyleSheet(f"background:#F1EEE8; color:#667085; {style}")
            return
        if result.ok:
            self.backup_label.setText(f"백업 {self._local_time(result.at)}")
            self.backup_label.setStyleSheet(f"background:#E9F7EF; color:#166534; {style}")
        else:
            self.backup_label.setText("백업 실패")
            self.backup_label.setStyleSheet(f"background:#FDECEC; color:#B91C1C; {style}")
        self.backup_label.setToolTip(result.message)

    @Slot()
    def run_backup(self, reason: str = "수동") -> bool:
        if getattr(self, "_closing", False) and reason != "종료":
            return False
        if self.backup_thread and self.backup_thread.isRunning():
            return False
        thread = QThread(self)
        worker = BackupWorker(
            BackupRunner(self.packaging, self.cache, self.config.backup, self.station)
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._backup_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._backup_thread_finished)
        self.backup_thread = thread
        self._backup_worker = worker
        self._backup_reason = reason
        thread.start()
        return True

    @Slot(object)
    def _backup_finished(self, result: BackupResult) -> None:
        self.last_backup = result
        self._refresh_backup_label()
        if not result.ok:
            self.statusBar().showMessage(result.message, 8000)
        elif getattr(self, "_backup_reason", "") == "수동":
            self._success(f"{result.message} · 실적 {result.rows:,}행")

    @Slot()
    def _backup_thread_finished(self) -> None:
        self.backup_thread = None
        if hasattr(self, "_backup_worker"):
            del self._backup_worker
        if getattr(self, "_closing", False):
            QTimer.singleShot(0, self.close)

    @Slot()
    def backup_now(self) -> None:
        if not self.run_backup("수동"):
            self.statusBar().showMessage("백업이 이미 진행 중입니다.", 3000)

    @Slot()
    def configure_backup(self) -> None:
        dialog = BackupSettingsDialog(self.config.backup, self.station, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.backup = dialog.settings()
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            self._error(f"설정을 저장하지 못했습니다: {exc} [BP-CFG-002]")
            return
        self._apply_backup_schedule()
        if self.config.backup.active:
            self._success(
                f"백업 설정 저장: {self.config.backup.directory} · "
                f"{self.config.backup.interval_minutes}분마다"
            )
            self.run_backup("수동")
        else:
            self._success("외부 백업을 사용하지 않습니다. 로컬 자동 백업은 유지됩니다.")

    def closeEvent(self, event) -> None:
        """종료 전에 마지막 백업을 시도한다.

        공유 폴더가 응답하지 않아도 종료가 막히지 않도록 기다리는 시간을 둔다.
        """
        self.autosave_timer.stop()
        self._save_draft()
        # Never destroy a live QThread. Its completion re-attempts closing.
        running = [t for t in (self.sync_thread, self.backup_thread) if t and t.isRunning()]
        if running:
            self.statusBar().showMessage("진행 중인 업데이트·백업이 끝나면 종료합니다.")
            self._closing = True
            event.ignore()
            return
        if not getattr(self, "_final_backup_requested", False):
            self._closing = True
            self._final_backup_requested = True
            if self.run_backup("종료"):
                event.ignore()
                return
        self.backup_timer.stop()
        self.backup_after_confirm.stop()
        super().closeEvent(event)

    def _show_initial_cache_state(self) -> None:
        info = self.cache.info()
        if info.product_count:
            age = self.cache.cache_age_hours()
            detail = f"상품 {info.product_count:,}개 · 버전 {info.data_version or '-'} · 최종 갱신 {self._local_time(info.synced_at) or '-'}"
            if age is not None and age > self.config.cache_max_age_hours:
                self.cache_blocked = False
                self._set_sync_state("CACHED", detail + " · 오래된 DB, 업데이트 권장")
            else:
                self.cache_blocked = False
                self._set_sync_state("CACHED", detail)
        else:
            self.cache_blocked = True
            self._set_sync_state("NO_DATA", "처음 사용하려면 상품정보 업데이트가 필요합니다.")

    def _load_job(self, job: dict) -> None:
        self._restoring_input = True
        try:
            self.shipment_input.blockSignals(True)
            self.shipment_input.setText(job["shipment_code"])
            self.shipment_input.blockSignals(False)
            self.job_id = job["job_id"]
            self.job_shipment = job["shipment_code"]
            self.last_saved = self.packaging.last_group(self.job_id)
            if not self.operator_input.text().strip():
                self.operator_input.setText(job["operator_name"])
            self._clear_scan(keep_message=True)
            self._refresh_next_box_label()
            self._refresh_shipment_view()
        finally:
            self._restoring_input = False

    def _restore_active_job(self) -> None:
        session = self.packaging.load_draft("active-job") or {}
        job = self.packaging.job(str(session.get("job_id") or ""))
        if job and job["status"] == "OPEN":
            self._load_job(job)

    @Slot()
    def show_history(self) -> None:
        dialog = JobHistoryDialog(self.packaging, self)
        if dialog.exec() != QDialog.Accepted or not self._guard_pending_input():
            return
        try:
            job = self.packaging.resume_job(dialog.selected_job_id, self.operator_input.text())
            self._load_job(job)
            self._success(f"출고건 {job['shipment_code']}의 이전 작업을 이어갑니다.")
        except BeyondPackError as exc:
            self._error(str(exc))

    @Slot()
    def finish_job(self) -> None:
        if not self._guard_pending_input():
            return
        if not self.job_id:
            self._error("완료할 작업이 없습니다. 이전 작업에서 선택하거나 박스를 먼저 확정하세요.")
            return
        if QMessageBox.question(self, "작업 완료", "현재 작업을 완료할까요? 완료 후에는 이력에서 조회·출력할 수 있습니다.") != QMessageBox.Yes:
            return
        try:
            self.packaging.close_job(self.job_id, self.operator_input.text())
        except BeyondPackError as exc:
            self._error(str(exc))
            return
        self.job_id = None
        self.job_shipment = ""
        self._refresh_shipment_view()
        self.backup_after_confirm.start()
        self._success("작업을 완료했습니다. '이전 작업'에서 기록을 확인할 수 있습니다.")

    @Slot()
    def new_job(self) -> None:
        if not self._guard_pending_input():
            return
        self.job_id = None
        self.job_shipment = ""
        self.last_saved = None
        self.packaging.clear_draft("active-job")
        self.shipment_input.clear()
        self._success("새 출고건 번호를 입력하세요. 이전 작업은 이력에서 이어갈 수 있습니다.")

    @Slot()
    def refresh_current_products(self) -> None:
        if not self.items:
            self._error("재확인할 구성품이 없습니다.")
            return
        try:
            updated = [BoxItem.from_product(self.cache.lookup(i.fnsku, i.country_code), i.qty_per_box) for i in self.items]
        except BeyondPackError as exc:
            self._error(f"{exc} 사용중지·미등록 상품은 구성품에서 제거한 뒤 다시 확인하세요.")
            return
        changes = "\n".join(f"{i.fnsku}: {i.item_code} / {i.sku} / {i.product_name}" for i in updated)
        if QMessageBox.question(self, "구성품 정보 재확인", changes + "\n\n현재 상품정보로 갱신할까요? 수량은 유지됩니다.") != QMessageBox.Yes:
            return
        self.items = updated
        self._refresh_items_table()
        self._save_draft()
        self._success("구성품 정보를 갱신했습니다. 실물 상품과 대조한 뒤 확정하세요.")

    @Slot()
    def restore_backup(self) -> None:
        if not self._guard_pending_input():
            return
        if any(t and t.isRunning() for t in (self.sync_thread, self.backup_thread)):
            self._error("업데이트·백업이 끝난 뒤 복원하세요.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "포장 백업 복원",
            str(self.config.resolved_data_dir / "backups"), "포장 DB (*.db)")
        if not filename:
            return
        if QMessageBox.question(self, "포장기록 전체 복원",
            "이 PC의 포장기록 전체를 선택한 백업 시점으로 되돌립니다.\n"
            "현재 기록은 복원 직전 안전 사본으로 보관됩니다. 다른 PC 기록과 합쳐지지 않습니다.\n계속할까요?") != QMessageBox.Yes:
            return
        if any(t and t.isRunning() for t in (self.sync_thread, self.backup_thread)):
            self._error("백업이 시작됐습니다. 완료된 뒤 다시 복원하세요.")
            return
        try:
            safety = restore_packaging_backup(self.packaging, Path(filename), self.operator_input.text())
        except Exception as exc:
            self._error(f"백업 복원 실패: {exc}")
            return
        self.job_id = None
        self.job_shipment = ""
        self.last_saved = None
        self.shipment_input.blockSignals(True)
        self.shipment_input.clear()
        self.shipment_input.blockSignals(False)
        self._restore_active_job()
        self._restore_draft()
        self._refresh_shipment_view()
        self._refresh_next_box_label()
        QMessageBox.information(self, "복원 완료", f"이전 작업을 조회할 수 있습니다.\n복원 직전 안전 사본: {safety}")

    def _selected_country_code(self) -> str:
        return str(self.country_combo.currentData() or "").strip().upper()

    def _select_country(self, country_code: str) -> bool:
        target = country_code.strip().upper()
        for index in range(self.country_combo.count()):
            if str(self.country_combo.itemData(index) or "").upper() == target:
                self.country_combo.setCurrentIndex(index)
                return True
        return False

    def _refresh_country_options(self, preferred_code: str = "") -> None:
        selected = preferred_code.strip().upper() or self._selected_country_code()
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        self.country_combo.addItem("국가 선택", "")
        for code, name in self.cache.available_countries():
            label = code if not name or name.upper() == code.upper() else f"{name} ({code})"
            self.country_combo.addItem(label, code)
        self.country_combo.blockSignals(False)
        if selected:
            self._select_country(selected)

    @Slot(int)
    def _country_changed(self, _index: int) -> None:
        selected = self._selected_country_code()
        locked = self.items[0].country_code if self.items else ""
        if locked and selected != locked:
            QMessageBox.warning(
                self,
                "작업 국가 변경 불가",
                "현재 박스에 구성품이 있습니다. 박스를 확정하거나 현재 입력을 초기화한 후 국가를 변경하세요.",
            )
            self.country_combo.blockSignals(True)
            self._select_country(locked)
            self.country_combo.blockSignals(False)
            return
        if self.current_product and selected != self.current_product.normalized_country_code:
            self._clear_scan(keep_message=True)
        if selected:
            self.next_action.setText(f"다음 행동: {self.country_combo.currentText()} 상품의 FNSKU를 스캔하세요.")
            self.fnsku_input.setFocus()
        else:
            self.next_action.setText("다음 행동: 작업 국가를 먼저 선택하세요.")
        self._save_draft()

    @Slot()
    def sync_now(self) -> None:
        if (
            self.config.source_type == "google_sheets"
            and not self.config.google_sheets.spreadsheet_url.strip()
        ):
            self.cache_blocked = not bool(self.cache.info().product_count)
            self._set_sync_state(
                "NO_DATA" if self.cache_blocked else "CACHED",
                "Google Sheet 주소가 없습니다. 'Sheet 설정'을 눌러 주소를 한 번 등록하세요.",
            )
            return
        self._start_sync(self.source_factory, "Google Sheet")

    def _start_sync(
        self,
        source_factory: Callable[[Callable[[str], None]], ProductSource],
        source_label: str,
    ) -> None:
        if self.sync_thread and self.sync_thread.isRunning():
            self.statusBar().showMessage("상품정보 업데이트가 이미 진행 중입니다.", 3000)
            return
        self._active_sync_label = source_label
        self._set_sync_state("SYNCING", f"{source_label}에서 최신 상품 CSV를 확인하고 있습니다.")
        self.update_button.setEnabled(False)
        self.excel_import_action.setEnabled(False)
        self.sheet_settings_button.setEnabled(False)
        thread = QThread(self)
        worker = SyncWorker(
            source_factory,
            self.cache,
            self.config.resolved_data_dir / "sync-status.json",
            self.config.large_drop_threshold,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.login_required.connect(self._show_login_message)
        worker.finished.connect(self._sync_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._sync_thread_finished)
        self.sync_thread = thread
        self._sync_worker = worker
        thread.start()

    @Slot(str)
    def _show_login_message(self, message: str) -> None:
        QMessageBox.information(self, "상품정보 안내", message)

    @Slot(object)
    def _sync_finished(self, result: SyncResult) -> None:
        self.update_button.setEnabled(True)
        self.excel_import_action.setEnabled(True)
        self.sheet_settings_button.setEnabled(True)
        self.cache_blocked = result.state == "NO_DATA"
        display_state = result.state
        self._set_sync_state(
            display_state,
            f"{result.message} · DB {result.cache.data_version or '-'} · {result.cache.product_count:,}개",
        )
        if result.state == "CURRENT":
            self._refresh_country_options()
            self._success("상품정보 업데이트 완료. 작업 국가를 선택하고 FNSKU를 스캔하세요.")
        else:
            self._error(result.message, beep=False)
        self.fnsku_input.setFocus()

    @Slot()
    def configure_google_sheet(self) -> None:
        if self.sync_thread and self.sync_thread.isRunning():
            self.statusBar().showMessage("업데이트가 끝난 뒤 설정을 변경하세요.", 3000)
            return
        current = self.config.google_sheets.spreadsheet_url
        value, accepted = QInputDialog.getText(
            self,
            "Google Sheet 설정",
            "BeyondPack 탭이 열린 Google Sheet 주소를 붙여넣으세요.",
            QLineEdit.Normal,
            current,
        )
        if not accepted:
            return
        value = value.strip()
        try:
            google_sheet_csv_url(value)
        except BeyondPackError as exc:
            self._error(f"{exc} [{exc.code}]")
            return
        self.config.source_type = "google_sheets"
        self.config.google_sheets.spreadsheet_url = value
        self.config.google_sheets.gid = ""
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            self._error(f"설정을 저장하지 못했습니다: {exc} [BP-CFG-002]")
            return
        self._success("Google Sheet 주소를 저장했습니다. 상품정보를 자동 업데이트합니다.")
        self.sync_now()

    @Slot()
    def import_excel_products(self) -> None:
        if self.sync_thread and self.sync_thread.isRunning():
            self.statusBar().showMessage("업데이트가 끝난 뒤 Excel을 가져오세요.", 3000)
            return
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "비상 상품 마스터 선택",
            str(Path.home()),
            "Excel 통합 문서 (*.xlsx)",
        )
        if not filename:
            return
        answer = QMessageBox.question(
            self,
            "Excel 비상 업데이트",
            "선택한 Excel을 검증한 뒤 로컬 상품DB에 적용합니다.\n"
            "Google Sheet 자동 업데이트 설정은 유지됩니다. 계속할까요?",
        )
        if answer != QMessageBox.Yes:
            return
        path = Path(filename)
        self._start_sync(lambda _notifier: ExcelProductSource(path), f"Excel({path.name})")

    @Slot()
    def _sync_thread_finished(self) -> None:
        self.sync_thread = None
        if hasattr(self, "_sync_worker"):
            del self._sync_worker
        if getattr(self, "_closing", False):
            QTimer.singleShot(0, self.close)

    def _set_sync_state(self, state: str, detail: str) -> None:
        background, foreground, title = COLORS.get(state, COLORS["ERROR"])
        self.sync_banner.setStyleSheet(
            f"QFrame#syncBanner {{background:{background}; border:1px solid {foreground}; border-radius:7px;}}"
        )
        self.sync_state_label.setText(f"● {title}")
        self.sync_state_label.setStyleSheet(f"font-weight:700; color:{foreground};")
        self.sync_detail_label.setText(detail)
        self.sync_detail_label.setStyleSheet(f"color:{foreground};")

    @Slot()
    def lookup_product(self) -> None:
        if self.cache_blocked:
            self._error(
                "사용 가능한 로컬 상품DB가 없습니다. 상품정보를 업데이트하세요. [BP-CACHE-001]"
            )
            return
        country_code = self._selected_country_code()
        if not country_code:
            self._error("먼저 작업 국가를 선택하세요. [BP-LOOKUP-003]")
            self.country_combo.setFocus()
            return
        try:
            product = self.cache.lookup(self.fnsku_input.text(), country_code)
        except BeyondPackError as exc:
            self.current_product = None
            self._clear_product_fields()
            self._error(f"{exc} [{exc.code}] 상품정보 업데이트 후 다시 스캔하세요.")
            self.fnsku_input.selectAll()
            self.fnsku_input.setFocus()
            return
        self.current_product = product
        for key, widget in self.product_fields.items():
            widget.setText(str(getattr(product, key)))
            widget.setToolTip(str(getattr(product, key)))
            if isinstance(widget, QLineEdit):
                widget.setCursorPosition(0)
        self.qty_input.setValue(1)
        self._success("상품을 확인했습니다. 박스당 수량을 입력하고 Enter를 누르세요.")
        self.qty_input.setFocus()
        self.qty_input.selectAll()

    @Slot()
    def add_current_item(self) -> None:
        if not self.current_product:
            self._error("먼저 FNSKU를 스캔하세요. [BP-UX-001]")
            self.fnsku_input.setFocus()
            return
        qty = self.qty_input.value()
        existing = next(
            (
                index
                for index, item in enumerate(self.items)
                if (item.fnsku, item.country_code)
                == (self.current_product.normalized_fnsku, self.current_product.normalized_country_code)
            ),
            None,
        )
        if existing is not None:
            answer = QMessageBox.question(
                self,
                "중복 스캔 확인",
                "같은 FNSKU가 이미 있습니다. 기존 수량에 더할까요?",
            )
            if answer != QMessageBox.Yes:
                self.fnsku_input.selectAll()
                self.fnsku_input.setFocus()
                return
            old = self.items[existing]
            self.items[existing] = BoxItem(**{**asdict(old), "qty_per_box": old.qty_per_box + qty})
        else:
            self.items.append(BoxItem.from_product(self.current_product, qty))
        self._refresh_items_table()
        self.work_tabs.setCurrentIndex(0)
        self._clear_scan(keep_message=True)
        self._success("구성품에 추가했습니다. 다음 FNSKU를 스캔하거나 포장정보를 입력하세요.")
        self._save_draft()
        self._focus_scan_input()

    # 인쇄 뒤 포커스를 되찾을 때까지 기다리는 시각(ms). 0은 이번 이벤트 처리
    # 직후, 뒤의 값은 프린터 드라이버 창이나 스풀러 알림이 늦게 떴다 사라진
    # 경우를 위한 것이다.
    FOCUS_RETRY_MS = (0, 300)

    def _focus_scan_input(self) -> None:
        """다음 박스를 바로 스캔할 수 있게 FNSKU 입력칸으로 커서를 돌려놓는다.

        `setFocus()` 한 번으로는 부족하다. 라벨을 인쇄하면 프린터 드라이버 창과
        스풀러 알림이 잠깐 앞으로 나왔다 사라지면서 포커스를 가져가는데, 그게
        이 함수가 끝난 뒤에 일어난다. 그래서 잠시 뒤에 다시 확인한다.

        다만 되돌리기 전에 지금 어디에 커서가 있는지 본다. 작업자가 스스로
        박스수량이나 무게 칸으로 옮겼다면 뺏지 않는다.
        """
        entries = (
            self.fnsku_input,
            self.qty_input,
            self.box_count,
            self.weight,
            self.length,
            self.width,
            self.height,
            self.shipment_input,
            self.operator_input,
        )

        def restore(force: bool) -> None:
            if not force and any(QApplication.focusWidget() is w for w in entries):
                return
            self.fnsku_input.setFocus(Qt.OtherFocusReason)
            self.fnsku_input.selectAll()

        restore(True)
        for delay in self.FOCUS_RETRY_MS:
            # 창을 문맥으로 넘겨 둔다. 인쇄 직후 프로그램을 닫으면 예약된
            # 호출이 이미 사라진 위젯을 건드리게 되는데, 이러면 취소된다.
            QTimer.singleShot(delay, self, lambda: restore(False))

    def remove_selected_item(self) -> None:
        row = self.items_table.currentRow()
        if row < 0:
            self._error("제거할 상품 행을 선택하세요. [BP-UX-002]", beep=False)
            return
        self.items.pop(row)
        self._refresh_items_table()
        self._save_draft()
        self.fnsku_input.setFocus()

    def _refresh_items_table(self) -> None:
        self.work_tabs.setTabText(
            0, f"02  박스 구성품 · {len(self.items)}" if self.items else "02  박스 구성품"
        )
        self.items_table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            values = [
                item.item_code,
                item.fnsku,
                item.country_name,
                item.product_name,
                str(item.qty_per_box),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if column == 4:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.items_table.setItem(row, column, cell)
        self.items_table.resizeRowsToContents()
        self._refresh_packing_summary()

    def _shipment_code(self) -> str:
        return normalize_shipment_code(self.shipment_input.text())

    @Slot()
    def _shipment_changed(self) -> None:
        """출고건이 바뀌면 다음 박스번호를 다시 계산한다.

        박스번호는 출고건 단위로 이어지므로, 다른 출고건으로 바꾸면 이어서
        저장하지 않도록 현재 작업을 끊는다.
        """
        code = self._shipment_code()
        if self.edit_group_id and code != self.job_shipment:
            self.shipment_input.blockSignals(True)
            self.shipment_input.setText(self.job_shipment)
            self.shipment_input.blockSignals(False)
            self._error("정정 중에는 출고건을 바꿀 수 없습니다. F4로 정정을 취소하세요.")
            return
        if self.job_id and code != self.job_shipment:
            self.job_id = None
        self._refresh_next_box_label()
        self._refresh_shipment_view()
        self._save_draft()

    @staticmethod
    def _local_time(value: object) -> str:
        """UTC로 저장한 시각을 현장 시간으로 보여 준다."""
        text = str(value or "")
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%m-%d %H:%M")

    @staticmethod
    def _decimal_text(value: object) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return Decimal(0)

    def _refresh_shipment_view(self) -> None:
        """확정된 박스를 출고건 단위로 누적해 보여 준다.

        작업자가 별도 파일을 열지 않고도 지금까지 몇 번 박스까지 나갔는지,
        각 박스에 무엇이 담겼는지 화면에서 바로 확인할 수 있게 한다.
        """
        code = self._shipment_code()
        groups = self.packaging.shipment_groups(code) if code else []
        self.progress_table.setRowCount(len(groups))
        total_boxes = 0
        total_weight = Decimal(0)
        for row, group in enumerate(groups):
            count = int(group["box_count"])
            start = int(group["box_start_no"])
            weight = self._decimal_text(group["weight_kg"])
            total_boxes += count
            total_weight += weight * count
            if int(group["item_count"] or 0) == 1:
                contents = str(group["first_fnsku"] or group["first_product_name"] or "-")
            else:
                contents = f"합포 {int(group['item_count'] or 0)}품목"
            values = [
                f"#{start}" if count == 1 else f"#{start}~#{start + count - 1}",
                contents,
                str(int(group["total_qty"] or 0)),
                f"{weight:g} kg",
                f"{self._decimal_text(group['length_cm']):g}×"
                f"{self._decimal_text(group['width_cm']):g}×"
                f"{self._decimal_text(group['height_cm']):g}",
                self._local_time(group["created_at"]),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column:
                    cell.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self.progress_table.setItem(row, column, cell)
            self.progress_table.item(row, 0).setData(Qt.UserRole, group["box_group_id"])
        self.progress_table.scrollToBottom()
        self._progress_selection_changed()
        self.work_tabs.setTabText(
            1,
            f"출고 현황 · {total_boxes:,}박스" if groups else "출고 현황",
        )
        if not code:
            self.progress_summary.setText("출고건을 입력하면 확정 내역을 확인할 수 있습니다.")
        elif groups:
            self.progress_summary.setText(
                f"{code} · 확정 {total_boxes:,}박스 · 총 중량 {total_weight:g} kg · "
                f"다음 박스 #{self.packaging.next_box_number(code)}"
            )
        else:
            self.progress_summary.setText(
                f"출고건 {code} · 아직 확정된 박스가 없습니다. 첫 박스는 #1입니다."
            )
        self.shipment_completer_model.setStringList(self.packaging.recent_shipments())

    @Slot()
    def _progress_selection_changed(self) -> None:
        row = self.progress_table.currentRow()
        selected = row >= 0
        # 중간 박스도 정정·취소할 수 있다. 번호를 당겨 1..N을 유지하고, 번호가
        # 밀린 박스는 재부착 목록으로 안내한다.
        editable = False
        is_last = selected and row == self.progress_table.rowCount() - 1
        if selected:
            saved = self.packaging.box_group(self._selected_box_group_id())
            job = self.packaging.job(saved[0]["job_id"]) if saved else None
            editable = bool(job and job["status"] == "OPEN")
        self.reprint_selected_button.setEnabled(selected)
        for button in (self.amend_selected_button, self.delete_selected_button):
            button.setEnabled(editable)
            button.setToolTip(
                ""
                if is_last or not editable
                else "중간 박스입니다. 고치면 뒤따르는 박스번호가 당겨지고 "
                "그 박스들의 라벨을 다시 붙여야 합니다."
            )

    # ---- 번호가 밀린 박스 재부착 ------------------------------------------

    RELABEL_KEY = "relabel"

    def _relabel_pending(self) -> dict:
        return self.packaging.load_draft(self.RELABEL_KEY) or {}

    def _register_relabel(self, moved: list[dict] | tuple[dict, ...]) -> None:
        """번호가 밀린 박스를 재부착 목록에 올린다.

        번호를 당기면 기록은 맞지만 상자에 붙어 있는 라벨은 옛 번호 그대로다.
        작업자가 어느 상자를 다시 붙여야 하는지 잊지 않도록, 끝났다고 누를
        때까지 화면에 남긴다. 프로그램을 껐다 켜도 남아야 하므로 DB에 둔다.
        """
        if not moved:
            return
        pending = self._relabel_pending()
        entries = list(pending.get("entries") or [])
        known = {entry.get("box_group_id") for entry in entries}
        for entry in moved:
            if entry["box_group_id"] in known:
                # 같은 박스가 또 밀렸다면 최종 번호만 갱신한다.
                for existing in entries:
                    if existing.get("box_group_id") == entry["box_group_id"]:
                        existing["new_start_no"] = entry["new_start_no"]
                        existing["new_end_no"] = entry["new_end_no"]
                continue
            entries.append(dict(entry))
            known.add(entry["box_group_id"])
        self.packaging.save_draft(
            self.RELABEL_KEY,
            {"shipment_code": self._shipment_code(), "entries": entries},
        )
        self._refresh_relabel_banner()

    def _refresh_relabel_banner(self) -> None:
        pending = self._relabel_pending()
        entries = pending.get("entries") or []
        if not entries:
            self.relabel_banner.setVisible(False)
            self.relabel_print_button.setVisible(False)
            self.relabel_done_button.setVisible(False)
            return
        boxes = sum(int(entry.get("box_count") or 0) for entry in entries)
        spans = ", ".join(
            f"#{entry['new_start_no']}"
            if entry["new_start_no"] == entry["new_end_no"]
            else f"#{entry['new_start_no']}~#{entry['new_end_no']}"
            for entry in sorted(entries, key=lambda e: e["new_start_no"])
        )
        self.relabel_banner.setText(
            f"재부착 필요 {boxes}박스 — 새 번호 {spans}. "
            "해당 상자의 옛 라벨을 떼고 새 라벨을 붙이세요."
        )
        self.relabel_banner.setVisible(True)
        self.relabel_print_button.setVisible(True)
        self.relabel_done_button.setVisible(True)

    @Slot()
    def print_relabel_batch(self) -> None:
        """재부착 대상 박스의 라벨을 새 번호로 한 번에 출력한다."""
        entries = self._relabel_pending().get("entries") or []
        if not entries:
            return
        printed = 0
        for entry in sorted(entries, key=lambda e: e["new_start_no"]):
            saved = self.packaging.box_group(str(entry["box_group_id"]))
            if not saved:
                continue
            group, items = saved
            if self._print_box_labels(group, items, ask=False):
                printed += int(group["box_count"])
        if printed:
            self._success(
                f"재부착 라벨 {printed}장을 출력했습니다. 옛 라벨을 떼고 붙인 뒤 "
                "'재부착 완료'를 누르세요."
            )
        else:
            self._error("재부착 라벨을 출력하지 못했습니다. 프린터를 확인하세요. [BP-PRINT-003]")
        self._focus_scan_input()

    @Slot()
    def finish_relabel(self) -> None:
        entries = self._relabel_pending().get("entries") or []
        boxes = sum(int(entry.get("box_count") or 0) for entry in entries)
        if boxes and QMessageBox.question(
            self,
            "재부착 완료",
            f"{boxes}박스의 라벨을 모두 새 번호로 바꿔 붙였습니까?\n"
            "확인을 누르면 안내가 사라집니다.",
        ) != QMessageBox.Yes:
            return
        self.packaging.clear_draft(self.RELABEL_KEY)
        self._refresh_relabel_banner()
        self._success("재부착을 완료로 표시했습니다.")

    def _confirm_renumber(self, moved: list[dict], what: str) -> bool:
        """번호가 밀리는 비용을 확정 전에 보여 주고 동의를 받는다."""
        if not moved:
            return True
        boxes = sum(int(entry["box_count"]) for entry in moved)
        spans = ", ".join(
            f"#{entry['old_start_no']}~#{entry['old_end_no']} → "
            f"#{entry['new_start_no']}~#{entry['new_end_no']}"
            for entry in moved[:6]
        )
        more = "" if len(moved) <= 6 else f" 외 {len(moved) - 6}묶음"
        return QMessageBox.question(
            self,
            "박스번호 당김 확인",
            f"{what}하면 뒤따르는 <b>{boxes}박스</b>의 번호가 당겨집니다.<br><br>"
            f"{spans}{more}<br><br>"
            f"<b>그 {boxes}박스의 라벨을 모두 다시 붙여야 합니다.</b> "
            "상자가 아직 손이 닿는 곳에 있는지 확인하세요. "
            "이미 실었거나 패킹리스트를 넘긴 뒤라면 번호가 서류와 어긋납니다.<br><br>"
            "계속할까요?",
        ) == QMessageBox.Yes

    def _selected_box_group_id(self) -> str:
        row = self.progress_table.currentRow()
        cell = self.progress_table.item(row, 0) if row >= 0 else None
        return str(cell.data(Qt.UserRole)) if cell else ""

    def _take_back_reason(self, title: str, group: dict) -> str:
        """되돌리는 이유를 받는다. 감사 기록에 남으므로 비워 둘 수 없다."""
        start = int(group["box_start_no"])
        count = int(group["box_count"])
        span = f"#{start}" if count == 1 else f"#{start}~#{start + count - 1}"
        reason, accepted = QInputDialog.getText(
            self,
            title,
            f"{span} 박스 {count}개의 {title} 작업입니다.\n"
            "정정은 재확정할 때 적용됩니다. 취소·정정 후 기존 라벨은 폐기하세요.\n\n사유를 입력하세요(기록에 남습니다):",
        )
        return reason.strip() if accepted else ""

    def _guard_pending_input(self) -> bool:
        if self.items or self.edit_group_id or self.current_product or any(
            w.value() for w in (self.box_count, self.weight, self.length, self.width, self.height)
        ):
            self._error(
                "작성 중인 박스 구성품이 있습니다. 확정하거나 F4로 초기화한 뒤 진행하세요. [BP-PACK-002]"
            )
            return False
        return True

    @Slot()
    def delete_selected_group(self) -> None:
        if not self._guard_pending_input():
            return
        box_group_id = self._selected_box_group_id()
        saved = self.packaging.box_group(box_group_id) if box_group_id else None
        if not saved:
            self._error("취소할 박스 행을 선택하세요. [BP-PACK-002]", beep=False)
            return
        group, _items = saved
        moved = self.packaging.renumber_preview(box_group_id)
        if not self._confirm_renumber(moved, "이 박스의 확정을 취소"):
            return
        reason = self._take_back_reason("선택 박스 확정 취소", group)
        if not reason:
            return
        self._take_back(box_group_id, reason, "CANCEL")

    @Slot()
    def amend_selected_group(self) -> None:
        """원본은 유지하고 정정 초안을 만든다. 교체는 확정 트랜잭션에서만 한다."""
        if not self._guard_pending_input():
            return
        box_group_id = self._selected_box_group_id()
        saved = self.packaging.box_group(box_group_id) if box_group_id else None
        if not saved:
            self._error("수정할 박스 행을 선택하세요. [BP-PACK-002]", beep=False)
            return
        group, _items = saved
        reason = self._take_back_reason("박스 정정", group)
        if not reason:
            return
        job = self.packaging.job(group["job_id"])
        if not job or job["status"] != "OPEN":
            self._error("진행 중인 작업의 박스만 정정할 수 있습니다.")
            return
        self.job_id = group["job_id"]
        self.job_shipment = group["shipment_code"]
        self.edit_group_id = box_group_id
        self.edit_reason = reason
        # 박스수량을 바꾸면 뒤 번호가 밀린다. 확정할 때 비교하려고 기억해 둔다.
        self.edit_box_count = int(group["box_count"])
        items = _items
        self.items = [
            BoxItem(
                fnsku=str(item["fnsku"]),
                item_code=str(item["item_code"]),
                sku=str(item["sku"]),
                country_code=str(item["country_code"]),
                country_name=str(item["country_name"]),
                product_name=str(item["product_name"]),
                qty_per_box=int(item["qty_per_box"]),
                source_modified_at=str(item.get("source_modified_at") or ""),
            )
            for item in items
        ]
        if self.items:
            self._select_country(self.items[0].country_code)
        self._refresh_items_table()
        self.box_count.setValue(int(group["box_count"]))
        for widget, key in (
            (self.weight, "weight_kg"),
            (self.length, "length_cm"),
            (self.width, "width_cm"),
            (self.height, "height_cm"),
        ):
            widget.setValue(float(self._decimal_text(group[key])))
        self.work_tabs.setCurrentIndex(0)
        self._save_draft()
        self._success(
            f"박스 #{int(group['box_start_no'])} 정정 중입니다. 원본은 확정 전까지 유지됩니다. "
            "고친 뒤 Ctrl+Enter로 확정하세요. F4를 누르면 원본을 유지하고 정정을 취소합니다."
        )

    def _take_back(
        self, box_group_id: str, reason: str, action: str
    ) -> tuple[dict, list[dict]] | None:
        operator_name = self.operator_input.text().strip()
        if not operator_name:
            self._error("작업자 이름 또는 사번을 입력하세요. [BP-PACK-001]")
            return None
        try:
            group, items, moved = self.packaging.take_back_box_group(
                box_group_id, operator_name, reason, action
            )
        except BeyondPackError as exc:
            self._error(f"{exc} [{exc.code}]")
            return None
        # 되돌린 박스가 F8 대상이면 대상을 다시 잡는다.
        self.last_saved = self.packaging.last_group(self.job_id) if self.job_id else None
        self._register_relabel(moved)
        self._refresh_next_box_label()
        self._refresh_shipment_view()
        self.backup_after_confirm.start()
        if action in {"DELETE", "CANCEL"}:
            start = int(group["box_start_no"])
            count = int(group["box_count"])
            span = f"#{start}" if count == 1 else f"#{start}~#{start + count - 1}"
            tail = (
                f"뒤 {sum(m['box_count'] for m in moved)}박스의 번호가 당겨졌습니다. "
                "아래 재부착 안내를 따르세요."
                if moved
                else f"다음 박스는 #{start}입니다."
            )
            self._success(
                f"박스 {span}의 확정을 취소했습니다. 원본은 이력에 보존됩니다. {tail} "
                "이미 출력한 라벨은 폐기하세요."
            )
        return group, items

    @Slot()
    def print_selected_group(self) -> None:
        row = self.progress_table.currentRow()
        if row < 0:
            self._error("재출력할 박스 행을 선택하세요. [BP-PRINT-001]", beep=False)
            return
        cell = self.progress_table.item(row, 0)
        saved = self.packaging.box_group(str(cell.data(Qt.UserRole))) if cell else None
        if not saved:
            self._error("선택한 박스를 찾을 수 없습니다. [BP-PRINT-001]", beep=False)
            return
        group, items = saved
        message = self._print_box_labels(
            group, items, ask=not self.config.label.printer_name.strip()
        )
        if message:
            self._success("재출력 " + message)
        # 재출력은 현황 표의 행을 고르고 누르므로 커서가 표에 가 있다.
        self._focus_scan_input()

    def _refresh_next_box_label(self) -> None:
        code = self._shipment_code()
        if not code:
            self.next_box_label.setText("출고건 입력")
            self.next_box_label.setStyleSheet(
                "background:#FDECEC; color:#B91C1C; border:1px solid #E6A2A2;"
                "border-radius:6px; padding:8px; font-weight:800;"
            )
            return
        self.next_box_label.setText(f"다음 #{self.packaging.next_box_number(code)}")
        self.next_box_label.setStyleSheet(
            "background:#EDF6F1; color:#285D46; border:1px solid #CADFD1;"
            "border-radius:8px; padding:10px; font-weight:700;"
        )

    @Slot()
    def confirm_box_group(self) -> None:
        try:
            if getattr(self, "_closing", False):
                return
            if self.sync_thread and self.sync_thread.isRunning():
                raise PackagingValidationError("상품정보 업데이트가 끝난 뒤 확정하세요.")
            if not self.items:
                raise PackagingValidationError("박스에 상품을 한 개 이상 추가하세요.")
            operator_name = self.operator_input.text().strip()
            if not operator_name:
                raise PackagingValidationError("작업자 이름 또는 사번을 입력하세요.")
            shipment_code = self._shipment_code()
            if not shipment_code:
                raise PackagingValidationError(
                    "출고건 번호를 입력하세요. 박스번호는 출고건 단위로 매겨집니다."
                )
            value = BoxGroupInput(
                box_count=positive_int(self.box_count.value(), "박스수량"),
                weight_kg=positive_decimal(self.weight.value(), "무게", Decimal(str(self.config.weight_max_kg))),
                length_cm=positive_decimal(self.length.value(), "가로", Decimal(str(self.config.dimension_max_cm))),
                width_cm=positive_decimal(self.width.value(), "세로", Decimal(str(self.config.dimension_max_cm))),
                height_cm=positive_decimal(self.height.value(), "높이", Decimal(str(self.config.dimension_max_cm))),
                items=tuple(self.items),
            )
            if self.edit_group_id and value.box_count != self.edit_box_count:
                preview = self.packaging.renumber_preview(
                    self.edit_group_id, value.box_count
                )
                if not self._confirm_renumber(preview, "박스수량을 바꿔 저장"):
                    return
            with self.cache.validated_items(value.items) as (checked, info):
                if not self.job_id or self.job_shipment != shipment_code:
                    self.job_id = self.packaging.create_job(
                        operator_name, info.data_version, __version__, shipment_code,
                    )
                    self.job_shipment = shipment_code
                saved = self.packaging.save_box_group(
                    self.job_id, replace(value, items=checked), operator_name,
                    product_db_version=info.data_version, verified_at=utc_now_iso(),
                    draft_key=self.DRAFT_KEY, replaces_group_id=self.edit_group_id,
                    reason=self.edit_reason,
                )
            self.last_saved = self.packaging.last_group(self.job_id)
        except BeyondPackError as exc:
            self._error(f"{exc} [{exc.code}]")
            self._focus_first_incomplete()
            return
        amended = bool(self.edit_group_id)
        self.edit_group_id = ""
        self.edit_reason = ""
        self.edit_box_count = 0
        self._register_relabel(saved.renumbered)
        self.items.clear()
        self._refresh_items_table()
        self._clear_scan(keep_message=True)
        self.box_count.setValue(0)
        for widget in (self.weight, self.length, self.width, self.height):
            widget.setValue(0)
        printed = ""
        if (
            self.config.label.auto_print
            and self.config.label.printer_name.strip()
            and self.last_saved
        ):
            group, items = self.last_saved
            printed = self._print_box_labels(group, items, ask=False)
        self._refresh_next_box_label()
        self._refresh_shipment_view()
        self.backup_after_confirm.start()
        self.work_tabs.setCurrentIndex(1)
        span = (
            f"#{saved.box_start_no}"
            if saved.box_start_no == saved.box_end_no
            else f"#{saved.box_start_no}~#{saved.box_end_no}"
        )
        if amended:
            tail = printed or "현황 탭에서 이 박스를 골라 라벨을 재출력하세요."
            if saved.renumbered:
                tail += (
                    f" 뒤 {sum(m['box_count'] for m in saved.renumbered)}박스의 번호가 "
                    "당겨졌습니다. 아래 재부착 안내를 따르세요."
                )
            self._success(f"박스 {span} 정정 저장. 박스번호는 그대로입니다. {tail}")
        else:
            self._success(
                f"박스 {span} 저장 완료. "
                + (printed if printed else "F8로 라벨을 출력하거나 다음 작업을 스캔하세요.")
            )
        self._focus_scan_input()

    @Slot()
    def reset_current(self) -> None:
        if self.items or any(w.value() for w in (self.weight, self.length, self.width, self.height)):
            if QMessageBox.question(self, "입력 초기화", "현재 입력을 모두 지울까요?") != QMessageBox.Yes:
                return
        self.edit_group_id = ""
        self.edit_reason = ""
        self.edit_box_count = 0
        self.items.clear()
        self._refresh_items_table()
        self._clear_scan(keep_message=True)
        self.box_count.setValue(0)
        for widget in (self.weight, self.length, self.width, self.height):
            widget.setValue(0)
        self.packaging.clear_draft(self.DRAFT_KEY)
        self.next_action.setStyleSheet("")
        self.next_action.setText("FNSKU를 스캔하세요.")
        self.fnsku_input.setFocus()

    def _label_printer(self) -> QPrinter | None:
        name = self.config.label.printer_name.strip()
        if name:
            info = QPrinterInfo.printerInfo(name)
            if info.isNull():
                self._error(
                    f"설정된 라벨 프린터 '{name}'를 찾을 수 없습니다. "
                    "'라벨 설정'에서 프린터를 다시 선택하세요. [BP-PRINT-002]"
                )
                return None
            printer = QPrinter(info, QPrinter.HighResolution)
        else:
            printer = QPrinter(QPrinter.HighResolution)
        apply_label_page(printer, self.config.label)
        return printer

    def _print_box_labels(self, group: dict, items: list[dict], ask: bool) -> str:
        """박스수량만큼의 라벨을 한 번의 인쇄 작업으로 순번대로 출력한다.

        박스마다 인쇄를 따로 호출하면 프린터 드라이버가 각각을 별개 작업으로
        처리해 박스번호가 이어지지 않는다. 하나의 인쇄 작업 안에서 페이지를
        직접 넘기며 라벨 1장씩 그린다.
        """
        numbers = box_numbers(group["box_start_no"], group["box_count"])
        printer = self._label_printer()
        if printer is None:
            return ""
        if ask:
            dialog = QPrintDialog(printer, self)
            if dialog.exec() != QPrintDialog.Accepted:
                return ""
            # 인쇄 대화상자가 용지를 A4로 되돌려도 라벨 규격을 다시 강제한다.
            apply_label_page(printer, self.config.label)
        try:
            print_box_labels(printer, group, items, numbers, self.config.label)
        except BeyondPackError as exc:
            self._error(f"{exc} [{exc.code}]")
            return ""
        except Exception as exc:
            self._error(f"라벨 출력 실패: {exc} [BP-PRINT-003]")
            return ""
        return f"라벨 #{numbers[0]}~#{numbers[-1]} {len(numbers)}장을 출력했습니다."

    @Slot()
    def print_last_labels(self) -> None:
        if not self.last_saved and self.job_id:
            self.last_saved = self.packaging.last_group(self.job_id)
        if not self.last_saved:
            self._error("재출력할 저장된 라벨이 없습니다. [BP-PRINT-001]", beep=False)
            return
        group, items = self.last_saved
        message = self._print_box_labels(
            group, items, ask=not self.config.label.printer_name.strip()
        )
        if message:
            self._success(message)
        self._focus_scan_input()

    @Slot()
    def configure_labels(self) -> None:
        dialog = LabelSettingsDialog(self.config.label, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.label = dialog.settings()
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            self._error(f"설정을 저장하지 못했습니다: {exc} [BP-CFG-002]")
            return
        label = self.config.label
        warning = self._label_page_warning()
        if warning:
            self._error(warning)
            return
        self._success(
            f"라벨 설정 저장: {label.printer_name or '인쇄할 때 선택'} · "
            f"{label.width_mm:g}×{label.height_mm:g}mm · "
            f"확정 시 자동출력 {'켬' if label.auto_print else '끔'}"
        )

    def _label_page_warning(self) -> str:
        """지정한 프린터가 라벨 용지 크기를 실제로 받아들이는지 확인한다."""
        label = self.config.label
        name = label.printer_name.strip()
        if not name:
            return ""
        info = QPrinterInfo.printerInfo(name)
        if info.isNull():
            return f"라벨 프린터 '{name}'를 찾을 수 없습니다. [BP-PRINT-002]"
        applied = apply_label_page(QPrinter(info, QPrinter.HighResolution), label)
        if label.matches_page(applied.width(), applied.height()):
            return ""
        return (
            f"'{name}'가 {label.width_mm:g}×{label.height_mm:g}mm 용지를 받아들이지 않습니다"
            f"(현재 {applied.width():.0f}×{applied.height():.0f}mm). "
            "Windows [설정 > 프린터 및 스캐너 > 인쇄 기본 설정]에서 이 크기의 "
            "사용자 정의 용지를 등록한 뒤 다시 저장하세요. [BP-PRINT-005]"
        )

    @Slot()
    def print_test_label(self) -> None:
        group = {
            "shipment_code": self._shipment_code() or "TEST",
            "box_start_no": 1,
            "box_count": 2,
            "weight_kg": "10.0",
            "length_cm": "10.0",
            "width_cm": "10.0",
            "height_cm": "10.0",
        }
        items = [
            {
                "fnsku": "TESTFNSKU1",
                "item_code": "TEST-CODE",
                "sku": "TEST-SKU",
                "country_code": "US",
                "country_name": "US",
                "qty_per_box": 1,
            }
        ]
        message = self._print_box_labels(
            group, items, ask=not self.config.label.printer_name.strip()
        )
        if message:
            self._success("테스트 " + message)
        self._focus_scan_input()

    @Slot()
    def export_current_job(self) -> None:
        # 한 출고건은 날짜와 프로그램 실행을 넘나들며 이어지므로 출고건 전체를 낸다.
        shipment = self._shipment_code()
        if not shipment:
            self._error("먼저 출고건 번호를 입력하세요. [BP-EXPORT-001]", beep=False)
            self.shipment_input.setFocus()
            return
        suggested = str(Path.home() / "Documents" / f"BeyondPack-{shipment}.xlsx")
        filename, _ = QFileDialog.getSaveFileName(
            self, f"출고건 {shipment} 포장실적 Excel 저장", suggested, "Excel (*.xlsx)"
        )
        if not filename:
            return
        try:
            count = export_shipment_xlsx(self.packaging, shipment, Path(filename))
        except Exception as exc:
            self._error(f"Excel 저장 실패: {exc} [BP-EXPORT-002]")
            return
        self._success(f"출고건 {shipment} Excel 저장 완료: {count}개 구성품 행")

    @Slot()
    def create_diagnostics(self) -> None:
        try:
            path = create_diagnostic_bundle(self.config.resolved_data_dir, Path.home() / "Desktop")
        except Exception as exc:
            self._error(f"진단파일 생성 실패: {exc} [BP-DIAG-001]")
            return
        QMessageBox.information(self, "진단파일 생성 완료", f"인증정보를 제외한 진단파일을 만들었습니다.\n{path}")

    def _save_draft(self) -> None:
        if self._restoring_input:
            return
        payload = {
            "job_id": self.job_id,
            "operator_name": self.operator_input.text().strip(),
            "edit_group_id": self.edit_group_id,
            "edit_reason": self.edit_reason,
            "items": [asdict(item) for item in self.items],
            "box_count": self.box_count.value(),
            "weight": self.weight.value(),
            "length": self.length.value(),
            "width": self.width.value(),
            "height": self.height.value(),
            "selected_country_code": self._selected_country_code(),
            "shipment_code": self._shipment_code(),
        }
        # 출고건이나 국가만 들어 있는 상태는 복구할 작업이 아니다. 이것까지
        # 저장하면 박스를 확정하고 정상 종료해도 다음 실행에서 복구 창이 뜬다.
        if self.items or self.edit_group_id or any(
            payload[key] for key in ("box_count", "weight", "length", "width", "height")
        ):
            self.packaging.save_draft(self.DRAFT_KEY, payload)
        else:
            self.packaging.clear_draft(self.DRAFT_KEY)

    def _restore_draft(self) -> None:
        draft = self.packaging.load_draft(self.DRAFT_KEY)
        if not draft:
            return
        answer = QMessageBox.question(
            self,
            "미완료 작업 복구",
            "이전에 저장하지 못한 포장 입력이 있습니다. 복구할까요?",
        )
        if answer != QMessageBox.Yes:
            self.packaging.clear_draft(self.DRAFT_KEY)
            return
        self._restoring_input = True
        try:
            self.shipment_input.setText(str(draft.get("shipment_code", "")))
            job = self.packaging.job(str(draft.get("job_id") or ""))
            if job and job["status"] == "OPEN" and job["shipment_code"] == self._shipment_code():
                self.job_id = job["job_id"]
                self.job_shipment = job["shipment_code"]
            self.operator_input.setText(str(draft.get("operator_name") or self.config.operator_name))
            self.edit_group_id = str(draft.get("edit_group_id") or "")
            self.edit_reason = str(draft.get("edit_reason") or "")
            self._select_country(str(draft.get("selected_country_code", "")))
            self.items = [BoxItem(**item) for item in draft.get("items", [])]
            if self.items:
                self._select_country(self.items[0].country_code)
            self.box_count.setValue(int(draft.get("box_count", 0)))
            self.weight.setValue(float(draft.get("weight", 0)))
            self.length.setValue(float(draft.get("length", 0)))
            self.width.setValue(float(draft.get("width", 0)))
            self.height.setValue(float(draft.get("height", 0)))
            self._refresh_items_table()
            self.next_action.setText("복구 완료: 구성품과 포장정보를 확인한 뒤 박스를 확정하세요.")
        except Exception as exc:
            self._error(f"미완료 입력을 복구하지 못했습니다. 원본 임시저장은 유지됩니다: {exc}")
        finally:
            self._restoring_input = False

    def _clear_scan(self, keep_message: bool = False) -> None:
        self.current_product = None
        self.fnsku_input.clear()
        self._clear_product_fields()
        self.qty_input.setValue(1)
        if not keep_message:
            self.next_action.setStyleSheet("")
            self.next_action.setText("FNSKU를 스캔하세요.")

    def _clear_product_fields(self) -> None:
        for field in self.product_fields.values():
            field.clear()

    def _success(self, message: str) -> None:
        self.next_action.setStyleSheet("background:#EDF6F1;color:#285D46;border:1px solid #CADFD1;border-radius:9px;")
        self.next_action.setText(message)
        self.statusBar().showMessage(message, 5000)

    def _error(self, message: str, beep: bool = True) -> None:
        if beep:
            QApplication.beep()
        self.next_action.setStyleSheet("background:#FBEEEE;color:#993E3E;border:1px solid #E9CCCC;border-radius:9px;")
        self.next_action.setText("확인 필요 · " + message)
        self.statusBar().showMessage(message, 8000)

    @staticmethod
    def _stylesheet() -> str:
        return stylesheet()
