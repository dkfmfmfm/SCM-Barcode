from __future__ import annotations

import argparse
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Callable

from .cache import ProductCacheRepository
from .config import AppConfig, load_config
from .errors import ConfigurationError
from .packaging import PackagingRepository
from .sources.base import ProductSource
from .sources.json_source import JsonProductSource
from .sources.google_sheets import GoogleSheetsProductSource


def build_source_factory(
    config: AppConfig, config_path: Path
) -> Callable[[Callable[[str], None]], ProductSource]:
    def factory(login_notifier: Callable[[str], None]) -> ProductSource:
        if config.source_type == "google_sheets":
            return GoogleSheetsProductSource(config.google_sheets)
        if not config.source_json_path.strip():
            raise ConfigurationError(
                "상품 소스가 설정되지 않았습니다. Google Sheet 주소 또는 JSON 경로를 지정하세요."
            )
        configured = Path(config.source_json_path).expanduser()
        if configured.is_absolute():
            path = configured
        else:
            path = config_path.parent / configured
            if not path.exists():
                cwd_candidate = Path.cwd() / configured
                if cwd_candidate.exists():
                    path = cwd_candidate
                elif config.source_json_path == "sample/products.json":
                    path = Path(resources.files("beyondpack").joinpath("resources/sample-products.json"))
        return JsonProductSource(path)

    return factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BeyondPack barcode packing workstation")
    parser.add_argument("--config", type=Path, help="config.json 경로")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="GUI 구성요소를 초기화한 뒤 종료합니다.",
    )
    args = parser.parse_args(argv)

    try:
        from PySide6.QtCore import QLockFile, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .ui import MainWindow
    except ImportError as exc:
        raise RuntimeError(
            "화면 구성요소를 불러올 수 없습니다. 프로그램을 다시 설치하세요."
        ) from exc

    app = QApplication(sys.argv[:1])
    app.setApplicationName("BeyondPack")
    app.setOrganizationName("BEYOND EARTH Co.,Ltd.")

    if args.self_test:
        with tempfile.TemporaryDirectory(
            prefix="beyondpack-self-test-", ignore_cleanup_errors=True
        ) as temp_dir:
            root = Path(temp_dir)
            sample_path = Path(
                resources.files("beyondpack").joinpath("resources/sample-products.json")
            )
            config = AppConfig(
                source_type="json",
                source_json_path=str(sample_path),
                data_dir=str(root / "data"),
                operator_name="SELF-TEST",
            )
            config_path = root / "config.json"
            cache = ProductCacheRepository(config.resolved_data_dir)
            packaging = PackagingRepository(config.resolved_data_dir / "packaging.db")
            window = MainWindow(
                config,
                config_path,
                cache,
                packaging,
                build_source_factory(config, config_path),
                auto_sync=False,
            )
            window.show()
            result = {"visible": False, "steppers": False}

            def finish_self_test() -> None:
                result["visible"] = window.isVisible() and "BeyondPack" in window.windowTitle()
                qty_before = window.qty_input.value()
                box_before = window.box_count.value()
                window.step_buttons["qty"][0].click()
                window.step_buttons["boxCount"][0].click()
                stepped_up = (
                    window.qty_input.value() == qty_before + 1
                    and window.box_count.value() == box_before + 1
                )
                window.step_buttons["qty"][1].click()
                window.step_buttons["boxCount"][1].click()
                result["steppers"] = (
                    stepped_up
                    and window.qty_input.value() == qty_before
                    and window.box_count.value() == box_before
                )
                window.close()
                app.quit()

            QTimer.singleShot(250, finish_self_test)
            QTimer.singleShot(5000, app.quit)
            app.exec()
            if not result["visible"] or not result["steppers"]:
                raise RuntimeError(
                    "GUI 창 또는 수량 증감 버튼 검사에 실패했습니다: "
                    f"visible={result['visible']}, steppers={result['steppers']}"
                )
            _self_test_labels(root / "labels.pdf")
        return 0

    config, config_path = load_config(args.config)
    data_dir = config.resolved_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(data_dir / "beyondpack.instance.lock"))
    instance_lock.setStaleLockTime(30_000)
    if not instance_lock.tryLock(100):
        QMessageBox.warning(
            None,
            "BeyondPack 실행 중",
            "BeyondPack이 이미 실행 중입니다. 기존 창을 사용하세요.\n"
            "창이 보이지 않으면 작업관리자에서 BeyondPack.exe를 종료한 뒤 다시 실행하세요.",
        )
        return 0
    cache = ProductCacheRepository(data_dir)
    packaging = PackagingRepository(data_dir / "packaging.db")
    window = MainWindow(
        config,
        config_path,
        cache,
        packaging,
        build_source_factory(config, config_path),
    )
    window.show()
    try:
        return app.exec()
    finally:
        instance_lock.unlock()


def _self_test_labels(output: Path) -> None:
    """박스수량만큼 라벨이 순번대로 1장씩 나오는지 실제 인쇄 경로로 검사한다."""
    import re

    from PySide6.QtPrintSupport import QPrinter

    from .config import LabelSettings
    from .labels import box_numbers
    from .printing import apply_label_page, print_box_labels

    group = {
        "box_start_no": 4,
        "box_count": 3,
        "weight_kg": "10.0",
        "length_cm": "10.0",
        "width_cm": "10.0",
        "height_cm": "10.0",
    }
    items = [
        {
            "fnsku": "SELFTESTFNSKU",
            "item_code": "SELF-TEST",
            "sku": "SELF-TEST-SKU",
            "country_code": "US",
            "country_name": "US",
            "qty_per_box": 1,
        }
    ]
    label = LabelSettings()
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(output))
    apply_label_page(printer, label)
    numbers = box_numbers(group["box_start_no"], group["box_count"])
    print_box_labels(printer, group, items, numbers)
    del printer

    payload = output.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", payload))
    if pages != len(numbers):
        raise RuntimeError(
            f"라벨 인쇄 검사 실패: 박스 {len(numbers)}개에 라벨 {pages}장이 생성되었습니다."
        )
    boxes = [
        tuple(round(float(value) * 25.4 / 72, 0) for value in match.split())
        for match in re.findall(rb"/MediaBox\s*\[([^\]]+)\]", payload)
    ]
    expected = (round(label.width_mm, 0), round(label.height_mm, 0))
    for box in boxes:
        actual = (round(box[2] - box[0], 0), round(box[3] - box[1], 0))
        if actual != expected:
            raise RuntimeError(
                f"라벨 용지 검사 실패: {actual[0]:.0f}x{actual[1]:.0f}mm "
                f"(기대 {expected[0]:.0f}x{expected[1]:.0f}mm)"
            )


if __name__ == "__main__":
    raise SystemExit(main())
