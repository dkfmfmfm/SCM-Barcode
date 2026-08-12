from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from beyondpack.config import LabelSettings, load_config


class ConfigTests(unittest.TestCase):
    def test_old_sharepoint_config_migrates_to_google_sheets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "source_type": "sharepoint",
                        "sharepoint": {"tenant_id": "old-secret"},
                    }
                ),
                encoding="utf-8",
            )
            config, _ = load_config(path)
            persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(config.source_type, "google_sheets")
        self.assertNotIn("sharepoint", persisted)

    def test_label_settings_default_to_the_field_label_stock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config, _ = load_config(Path(temp_dir) / "config.json")
        self.assertEqual((config.label.width_mm, config.label.height_mm), (40.0, 25.0))


class LabelPageTests(unittest.TestCase):
    def setUp(self):
        self.label = LabelSettings(width_mm=40.0, height_mm=25.0)

    def test_applied_page_matching_the_label_is_accepted(self):
        self.assertTrue(self.label.matches_page(40.0, 25.0))

    def test_driver_rounding_is_tolerated(self):
        self.assertTrue(self.label.matches_page(39.8, 25.1))

    def test_a4_fallback_is_rejected(self):
        # 드라이버에 해당 사용자 정의 용지가 없으면 크기 지정이 조용히 실패하고
        # A4가 유지된다. 이 상태로 인쇄하면 라벨이 잘리고 빈 라벨이 배출된다.
        self.assertFalse(self.label.matches_page(210.0, 297.0))

    def test_similar_but_wrong_stock_is_rejected(self):
        self.assertFalse(self.label.matches_page(40.0, 30.0))


if __name__ == "__main__":
    unittest.main()
