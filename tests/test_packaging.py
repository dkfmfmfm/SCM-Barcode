import tempfile
import unittest
import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from beyondpack.models import BoxGroupInput, BoxItem
from beyondpack.packaging import PackagingRepository


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = PackagingRepository(Path(self.temp.name) / "packaging.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_mixed_box_snapshot_and_box_numbering(self):
        job = self.repo.create_job("작업자1", "V1", "2.0.0")
        items = (
            BoxItem("X1", "A1", "SKU1", "US", "미국", "상품1", 2),
            BoxItem("X2", "A2", "SKU2", "JP", "일본", "상품2", 1),
        )
        group = BoxGroupInput(3, Decimal("8.4"), Decimal("42"), Decimal("31"), Decimal("24"), items)
        first = self.repo.save_box_group(job, group, "작업자1")
        second = self.repo.save_box_group(job, group, "작업자1")
        self.assertEqual((first.box_start_no, first.box_end_no), (1, 3))
        self.assertEqual((second.box_start_no, second.box_end_no), (4, 6))
        rows = self.repo.job_rows(job)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["fnsku"] for row in rows}, {"X1", "X2"})
        self.assertEqual(rows[0]["product_db_version"], "V1")

    def test_draft_round_trip(self):
        payload = {"items": [{"fnsku": "X1"}], "weight": 8.4}
        self.repo.save_draft("current", payload)
        self.assertEqual(self.repo.load_draft("current"), payload)
        self.repo.clear_draft("current")
        self.assertIsNone(self.repo.load_draft("current"))

    def test_database_connections_are_closed_after_each_operation(self):
        real_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch("beyondpack.packaging.sqlite3.connect", side_effect=tracked_connect):
            self.repo.save_draft("current", {"fnsku": "X1"})
            self.repo.load_draft("current")

        self.assertEqual(len(opened), 2)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
