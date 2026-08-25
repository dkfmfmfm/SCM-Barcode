from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QStringListModel, Qt, QThread, QTimer, Signal, Slot
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
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .cache import ProductCacheRepository
from .config import AppConfig, LabelSettings, save_config
from .diagnostics import create_diagnostic_bundle
from .errors import BeyondPackError, PackagingValidationError
from .exporter import export_shipment_xlsx
from .labels import box_numbers
from .models import BoxGroupInput, BoxItem, Product
from .normalization import normalize_shipment_code, positive_decimal, positive_int
from .packaging import PackagingRepository
from .printing import apply_label_page, print_box_labels
from .sources.base import ProductSource
from .sources.excel_source import ExcelProductSource
from .sources.google_sheets import google_sheet_csv_url
from .sync import ProductSyncService, SyncResult


COLORS = {
    "CURRENT": ("#E9F7EF", "#166534", "최신"),
    "CACHED": ("#FFF7E6", "#9A5B00", "캐시 사용 중"),
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

        guide = QLabel(
            "라벨 롤의 실제 크기를 mm로 입력하세요. 크기가 맞지 않으면 내용이 잘리거나 "
            "빈 라벨이 함께 배출됩니다. 저장 후 '테스트 라벨 출력'으로 확인하세요."
        )
        guide.setWordWrap(True)
        guide.setObjectName("fieldCaption")

        self.support_label = QLabel()
        self.support_label.setWordWrap(True)
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
        self.last_saved: tuple[dict, list[dict]] | None = None
        self.sync_thread: QThread | None = None
        self.cache_blocked = False

        self.setWindowTitle(f"BeyondPack {__version__} · BEYOND EARTH")
        self.setMinimumSize(1120, 760)
        self.resize(1280, 860)
        self._build_ui()
        self._build_actions()
        self._connect_autosave()
        self._refresh_country_options()
        self._restore_draft()
        self._refresh_next_box_label()
        self._refresh_shipment_view()
        self._show_initial_cache_state()
        if auto_sync:
            QTimer.singleShot(150, self.sync_now)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("BEYOND PACK")
        title.setObjectName("brandTitle")
        subtitle = QLabel("오프라인 우선 포장 작업")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        header.addWidget(QLabel("작업 국가"))
        self.country_combo = QComboBox()
        self.country_combo.setMinimumWidth(132)
        self.country_combo.setMinimumHeight(42)
        self.country_combo.setObjectName("countrySelector")
        self.country_combo.currentIndexChanged.connect(self._country_changed)
        header.addWidget(self.country_combo)
        header.addWidget(QLabel("작업자"))
        self.operator_input = QLineEdit(self.config.operator_name)
        self.operator_input.setPlaceholderText("이름 또는 사번")
        self.operator_input.setMaximumWidth(112)
        header.addWidget(self.operator_input)
        header.addWidget(QLabel("출고건"))
        self.shipment_input = QLineEdit()
        self.shipment_input.setObjectName("shipmentInput")
        self.shipment_input.setPlaceholderText("출고건 번호를 스캔·입력")
        self.shipment_input.setMaximumWidth(150)
        self.shipment_input.setMinimumHeight(42)
        self.shipment_input.setClearButtonEnabled(True)
        self.shipment_completer_model = QStringListModel(self)
        completer = QCompleter(self.shipment_completer_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.shipment_input.setCompleter(completer)
        self.shipment_input.textChanged.connect(self._shipment_changed)
        header.addWidget(self.shipment_input)
        self.next_box_label = QLabel()
        self.next_box_label.setObjectName("nextBox")
        self.next_box_label.setMinimumWidth(96)
        self.next_box_label.setAlignment(Qt.AlignCenter)
        header.addWidget(self.next_box_label)
        self.update_button = QPushButton("F2  상품 업데이트")
        self.update_button.clicked.connect(self.sync_now)
        header.addWidget(self.update_button)
        self.sheet_settings_button = QPushButton("Sheet 설정")
        self.sheet_settings_button.clicked.connect(self.configure_google_sheet)
        header.addWidget(self.sheet_settings_button)
        layout.addLayout(header)

        self.sync_banner = QFrame()
        self.sync_banner.setObjectName("syncBanner")
        banner_layout = QHBoxLayout(self.sync_banner)
        banner_layout.setContentsMargins(14, 9, 14, 9)
        self.sync_state_label = QLabel()
        self.sync_detail_label = QLabel()
        self.sync_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        banner_layout.addWidget(self.sync_state_label)
        banner_layout.addWidget(self.sync_detail_label, 1)
        layout.addWidget(self.sync_banner)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(12)
        right = QVBoxLayout()
        right.setSpacing(12)
        body.addLayout(left, 6)
        body.addLayout(right, 4)

        lookup_group = QGroupBox("1. 상품 스캔")
        lookup_layout = QVBoxLayout(lookup_group)
        scan_row = QHBoxLayout()
        self.fnsku_input = QLineEdit()
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
        self.product_fields: dict[str, QLineEdit] = {}
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
            value = QLineEdit()
            value.setReadOnly(True)
            value.setObjectName("readonlyField")
            value.setMinimumHeight(38)
            self.product_fields[key] = value
            box.addWidget(caption)
            box.addWidget(value)
            product_grid.addLayout(box, row, column, row_span, col_span)
        lookup_layout.addLayout(product_grid)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("박스당 상품수량"))
        self.qty_input = QSpinBox()
        self.qty_input.setObjectName("qtyInput")
        self.qty_input.setRange(1, 999999)
        self.qty_input.setSingleStep(1)
        self.qty_input.setValue(1)
        self.qty_input.setSuffix(" EA")
        add_row.addWidget(self._stepper(self.qty_input, "qty", "박스당 상품수량"))
        self.add_item_button = QPushButton("합포 구성에 추가")
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
        self.items_table = QTableWidget(0, 6)
        self.items_table.setHorizontalHeaderLabels(["FNSKU", "품목코드", "SKU", "국가", "품목명", "EA/BOX"])
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        header_view = self.items_table.horizontalHeader()
        for col in range(6):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.Stretch)
        self.items_table.setMinimumHeight(84)
        items_layout.addWidget(self.items_table)
        remove_button = QPushButton("선택 상품 제거")
        remove_button.clicked.connect(self.remove_selected_item)
        items_layout.addWidget(remove_button, alignment=Qt.AlignRight)
        self.work_tabs.addTab(items_page, "2. 박스 구성품")

        progress_page = QWidget()
        progress_layout = QVBoxLayout(progress_page)
        progress_layout.setContentsMargins(0, 10, 0, 0)
        self.progress_summary = QLabel()
        self.progress_summary.setObjectName("progressSummary")
        self.progress_summary.setWordWrap(True)
        progress_layout.addWidget(self.progress_summary)
        self.progress_table = QTableWidget(0, 6)
        self.progress_table.setHorizontalHeaderLabels(
            ["박스번호", "구성품", "EA/BOX", "무게", "규격(가로×세로×높이)", "확정시각"]
        )
        self.progress_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.progress_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.progress_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.progress_table.verticalHeader().setVisible(False)
        self.progress_table.setAlternatingRowColors(True)
        self.progress_table.setMinimumHeight(104)
        progress_header = self.progress_table.horizontalHeader()
        for column in range(6):
            progress_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        progress_header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.progress_table.itemSelectionChanged.connect(self._progress_selection_changed)
        progress_layout.addWidget(self.progress_table)
        self.reprint_selected_button = QPushButton("선택 박스 라벨 재출력")
        self.reprint_selected_button.setEnabled(False)
        self.reprint_selected_button.clicked.connect(self.print_selected_group)
        progress_layout.addWidget(self.reprint_selected_button, alignment=Qt.AlignRight)
        self.work_tabs.addTab(progress_page, "4. 출고건 작업 현황")
        left.addWidget(self.work_tabs, 1)

        package_group = QGroupBox("3. 포장정보 입력")
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

        self.confirm_button = QPushButton("Ctrl+Enter  박스 확정")
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.setMinimumHeight(54)
        self.confirm_button.clicked.connect(self.confirm_box_group)
        right.addWidget(self.confirm_button)

        utility_group = QGroupBox("작업 도구")
        utility_layout = QGridLayout(utility_group)
        reset_button = QPushButton("F4  현재 입력 초기화")
        reset_button.clicked.connect(self.reset_current)
        print_button = QPushButton("F8  마지막 라벨 재출력")
        print_button.clicked.connect(self.print_last_labels)
        export_button = QPushButton("출고건 Excel 저장")
        export_button.clicked.connect(self.export_current_job)
        # 설정·비상 업데이트·진단은 작업 중에 쓰지 않는다. 한 버튼에 모아
        # 오작동을 줄이고 작업 화면의 세로 공간을 비운다.
        admin_button = QPushButton("설정·관리자 도구  ▾")
        admin_menu = QMenu(admin_button)
        for text, callback in (
            ("라벨 설정", self.configure_labels),
            ("테스트 라벨 출력", self.print_test_label),
            ("Excel 비상 업데이트", self.import_excel_products),
            ("관리자 진단파일 생성", self.create_diagnostics),
        ):
            admin_menu.addAction(text, callback)
        self.excel_import_action = admin_menu.actions()[2]
        admin_button.setMenu(admin_menu)
        utility_layout.addWidget(reset_button, 0, 0)
        utility_layout.addWidget(print_button, 0, 1)
        utility_layout.addWidget(export_button, 1, 0)
        utility_layout.addWidget(admin_button, 1, 1)
        right.addWidget(utility_group)
        right.addStretch()

        self.next_action = QLabel("다음 행동: FNSKU를 스캔하세요.")
        self.next_action.setObjectName("nextAction")
        self.next_action.setWordWrap(True)
        right.addWidget(self.next_action)
        layout.addLayout(body, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("준비")
        self.setStyleSheet(self._stylesheet())

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

    def _connect_autosave(self) -> None:
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(350)
        self.autosave_timer.timeout.connect(self._save_draft)
        for widget in (self.box_count, self.weight, self.length, self.width, self.height):
            widget.valueChanged.connect(lambda _value: self.autosave_timer.start())

    def _show_initial_cache_state(self) -> None:
        info = self.cache.info()
        if info.product_count:
            age = self.cache.cache_age_hours()
            detail = f"DB {info.data_version or '-'} · {info.product_count:,}개 · 마지막 성공 {info.synced_at or '-'}"
            if age is not None and age > self.config.cache_max_age_hours:
                self.cache_blocked = False
                self._set_sync_state("CACHED", detail + " · 오래된 DB, 업데이트 권장")
            else:
                self.cache_blocked = False
                self._set_sync_state("CACHED", detail)
        else:
            self.cache_blocked = True
            self._set_sync_state("NO_DATA", "처음 사용하려면 상품정보 업데이트가 필요합니다.")

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
        self.qty_input.setValue(1)
        self._success("상품 확인 완료. 박스당 상품수량을 입력하고 합포 구성에 추가하세요.")
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
            0, f"2. 박스 구성품 ({len(self.items)})" if self.items else "2. 박스 구성품"
        )
        self.items_table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            values = [item.fnsku, item.item_code, item.sku, item.country_name, item.product_name, str(item.qty_per_box)]
            for column, value in enumerate(values):
                self.items_table.setItem(row, column, QTableWidgetItem(value))

    def _shipment_code(self) -> str:
        return normalize_shipment_code(self.shipment_input.text())

    @Slot()
    def _shipment_changed(self) -> None:
        """출고건이 바뀌면 다음 박스번호를 다시 계산한다.

        박스번호는 출고건 단위로 이어지므로, 다른 출고건으로 바꾸면 이어서
        저장하지 않도록 현재 작업을 끊는다.
        """
        code = self._shipment_code()
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
                contents = str(group["first_item_code"] or group["first_product_name"] or "-")
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
            f"4. 출고건 작업 현황 ({total_boxes}박스)" if groups else "4. 출고건 작업 현황",
        )
        if not code:
            self.progress_summary.setText("출고건 번호를 입력하면 이 출고건의 작업 현황이 표시됩니다.")
        elif groups:
            self.progress_summary.setText(
                f"출고건 {code} · 확정 {total_boxes}박스 · 총 중량 {total_weight:g} kg · "
                f"다음 박스 #{self.packaging.next_box_number(code)}"
            )
        else:
            self.progress_summary.setText(
                f"출고건 {code} · 아직 확정된 박스가 없습니다. 첫 박스는 #1입니다."
            )
        self.shipment_completer_model.setStringList(self.packaging.recent_shipments())

    @Slot()
    def _progress_selection_changed(self) -> None:
        self.reprint_selected_button.setEnabled(self.progress_table.currentRow() >= 0)

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
            "background:#EAF2FF; color:#1D4ED8; border:1px solid #9BBDF7;"
            "border-radius:6px; padding:8px; font-weight:800;"
        )

    @Slot()
    def confirm_box_group(self) -> None:
        try:
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
            if not self.job_id or self.job_shipment != shipment_code:
                self.job_id = self.packaging.create_job(
                    operator_name,
                    self.cache.info().data_version,
                    __version__,
                    shipment_code,
                )
                self.job_shipment = shipment_code
            saved = self.packaging.save_box_group(
                self.job_id, value, operator_name
            )
            self.last_saved = self.packaging.last_group(self.job_id)
        except BeyondPackError as exc:
            self._error(f"{exc} [{exc.code}]")
            return
        self.packaging.clear_draft(self.DRAFT_KEY)
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
        self.work_tabs.setCurrentIndex(1)
        self._success(
            f"박스 #{saved.box_start_no}~#{saved.box_end_no} 저장 완료. "
            + (printed if printed else "F8로 라벨을 출력하거나 다음 작업을 스캔하세요.")
        )
        self.fnsku_input.setFocus()

    @Slot()
    def reset_current(self) -> None:
        if self.items or any(w.value() for w in (self.weight, self.length, self.width, self.height)):
            if QMessageBox.question(self, "입력 초기화", "현재 입력을 모두 지울까요?") != QMessageBox.Yes:
                return
        self.items.clear()
        self._refresh_items_table()
        self._clear_scan(keep_message=True)
        self.box_count.setValue(0)
        for widget in (self.weight, self.length, self.width, self.height):
            widget.setValue(0)
        self.packaging.clear_draft(self.DRAFT_KEY)
        self.next_action.setText("다음 행동: FNSKU를 스캔하세요.")
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
        payload = {
            "items": [asdict(item) for item in self.items],
            "box_count": self.box_count.value(),
            "weight": self.weight.value(),
            "length": self.length.value(),
            "width": self.width.value(),
            "height": self.height.value(),
            "selected_country_code": self._selected_country_code(),
            "shipment_code": self._shipment_code(),
        }
        if (
            self.items
            or payload["shipment_code"]
            or any(payload[key] for key in ("weight", "length", "width", "height"))
        ):
            self.packaging.save_draft(self.DRAFT_KEY, payload)

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
        try:
            self.shipment_input.setText(str(draft.get("shipment_code", "")))
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
        except Exception:
            self.packaging.clear_draft(self.DRAFT_KEY)

    def _clear_scan(self, keep_message: bool = False) -> None:
        self.current_product = None
        self.fnsku_input.clear()
        self._clear_product_fields()
        self.qty_input.setValue(1)
        if not keep_message:
            self.next_action.setText("다음 행동: FNSKU를 스캔하세요.")

    def _clear_product_fields(self) -> None:
        for field in self.product_fields.values():
            field.clear()

    def _success(self, message: str) -> None:
        QApplication.beep()
        self.next_action.setStyleSheet("background:#E9F7EF;color:#166534;border:1px solid #86C89A;border-radius:7px;padding:12px;font-weight:700;")
        self.next_action.setText("정상 · " + message)
        self.statusBar().showMessage(message, 5000)

    def _error(self, message: str, beep: bool = True) -> None:
        if beep:
            QApplication.beep()
        self.next_action.setStyleSheet("background:#FDECEC;color:#B91C1C;border:1px solid #E6A2A2;border-radius:7px;padding:12px;font-weight:700;")
        self.next_action.setText("확인 필요 · " + message)
        self.statusBar().showMessage(message, 8000)

    @staticmethod
    def _stylesheet() -> str:
        return """
        QMainWindow, QWidget { background: #F7F5F1; color: #0B1F3A; font-family: 'Pretendard', 'Malgun Gothic'; font-size: 14px; }
        QLabel#brandTitle { font-size: 20px; font-weight: 800; letter-spacing: 1px; }
        QLabel#subtitle { color: #667085; }
        QGroupBox { background: white; border: 1px solid #DDD8CE; border-radius: 9px; margin-top: 13px; padding: 13px; font-weight: 700; }
        QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #CFC8BC; border-radius: 6px; padding: 7px; font-size: 16px; }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 2px solid #2563EB; }
        QWidget#numericStepper { background: transparent; }
        QPushButton[stepperButton="true"] { background:#F8FAFC; border:1px solid #AEB7C4; border-radius:4px; padding:0; font-size:11px; font-weight:800; }
        QPushButton[stepperButton="true"]:hover { background:#EAF2FF; border-color:#2563EB; }
        QPushButton[stepperButton="true"]:pressed { background:#BFDBFE; }
        QComboBox#countrySelector { background: #FFF7E6; border: 2px solid #D97706; font-weight: 800; }
        QLineEdit#shipmentInput { background: #FFF7E6; border: 2px solid #D97706; font-weight: 800; }
        QLineEdit#readonlyField { background: #F1F4F8; color: #0B1F3A; font-weight: 650; }
        QLabel#fieldCaption { color: #667085; font-size: 12px; }
        QPushButton { background: white; border: 1px solid #CFC8BC; border-radius: 6px; padding: 9px 13px; font-weight: 650; }
        QPushButton:hover { background: #F1EEE8; }
        QPushButton#primaryButton { background: #0B1F3A; color: white; border: 0; }
        QPushButton#confirmButton { background: #2563EB; color: white; border: 0; font-size: 17px; }
        QPushButton:disabled { background: #E5E7EB; color: #9CA3AF; }
        QTableWidget { background: white; border: 1px solid #DDD8CE; gridline-color: #E8E3DA; alternate-background-color: #FAF8F4; }
        QHeaderView::section { background: #0B1F3A; color: white; padding: 8px; border: 0; font-weight: 700; }
        QTabWidget::pane { background: white; border: 1px solid #DDD8CE; border-radius: 9px; }
        QTabBar::tab { background: #EFEBE3; border: 1px solid #DDD8CE; border-bottom: 0; border-top-left-radius: 7px; border-top-right-radius: 7px; padding: 9px 16px; margin-right: 4px; font-weight: 700; color: #667085; }
        QTabBar::tab:selected { background: white; color: #0B1F3A; }
        QLabel#progressSummary { background:#F1F4F8; color:#0B1F3A; border:1px solid #DDD8CE; border-radius:6px; padding:8px; font-weight:700; }
        QLabel#nextAction { background:#EAF2FF; color:#1D4ED8; border:1px solid #9BBDF7; border-radius:7px; padding:12px; font-weight:700; }
        """
