from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from beyondpack.config import load_config


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


if __name__ == "__main__":
    unittest.main()
