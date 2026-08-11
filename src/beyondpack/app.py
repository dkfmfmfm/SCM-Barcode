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
from .sources.sharepoint import SharePointProductSource


def build_source_factory(
    config: AppConfig, config_path: Path
) -> Callable[[Callable[[str], None]], ProductSource]:
    def factory(login_notifier: Callable[[str], None]) -> ProductSource:
        if config.source_type == "sharepoint":
            return SharePointProductSource(
                config.sharepoint,
                config.resolved_data_dir / "msal-token.cache",
                login_notifier,
            )
        if not config.source_json_path.strip():
            raise ConfigurationError(
                "상품 소스가 설정되지 않았습니다. config.json에 SharePoint 또는 JSON 경로를 지정하세요."
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
        from PySide6.QtWidgets import QApplication
        from .ui import MainWindow
    except ImportError as exc:
        raise RuntimeError(
            "화면 구성요소를 불러올 수 없습니다. 프로그램을 다시 설치하세요."
        ) from exc

    app = QApplication(sys.argv[:1])
    app.setApplicationName("BeyondPack")
    app.setOrganizationName("BEYOND EARTH Co.,Ltd.")

    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="beyondpack-self-test-") as temp_dir:
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
            app.processEvents()
            if not window.isVisible() or "BeyondPack" not in window.windowTitle():
                raise RuntimeError("GUI 창 초기화 검사에 실패했습니다.")
            window.close()
            app.processEvents()
        return 0

    config, config_path = load_config(args.config)
    data_dir = config.resolved_data_dir
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
