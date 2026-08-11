import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import beyondpack_entry


class BootstrapTests(unittest.TestCase):
    def test_startup_log_is_written_to_application_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"XDG_DATA_HOME": temp_dir}):
                path = beyondpack_entry._write_startup_log("TEST START")
            self.assertEqual(
                path,
                Path(temp_dir) / "BeyondPack" / "logs" / "startup.log",
            )
            self.assertIn("TEST START", path.read_text(encoding="utf-8"))

    def test_large_startup_log_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "BeyondPack" / "logs"
            log_dir.mkdir(parents=True)
            current = log_dir / "startup.log"
            current.write_bytes(b"x" * 1_000_001)
            with patch.dict(os.environ, {"XDG_DATA_HOME": temp_dir}):
                path = beyondpack_entry._write_startup_log("AFTER ROTATION")
            self.assertLess(path.stat().st_size, 1_000_000)
            self.assertTrue((log_dir / "startup.previous.log").exists())


if __name__ == "__main__":
    unittest.main()
