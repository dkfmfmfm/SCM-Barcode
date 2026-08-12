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

    def _group(self, box_count: int = 3) -> BoxGroupInput:
        items = (BoxItem("X1", "A1", "SKU1", "US", "미국", "상품1", 2),)
        return BoxGroupInput(
            box_count, Decimal("8.4"), Decimal("42"), Decimal("31"), Decimal("24"), items
        )

    def test_box_numbers_continue_within_the_same_shipment(self):
        first_job = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        first = self.repo.save_box_group(first_job, self._group(), "작업자1")
        # 프로그램을 다시 실행하면 새 작업이 만들어지지만 출고건은 같다.
        second_job = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        second = self.repo.save_box_group(second_job, self._group(), "작업자1")
        self.assertEqual((first.box_start_no, first.box_end_no), (1, 3))
        self.assertEqual((second.box_start_no, second.box_end_no), (4, 6))

    def test_a_different_shipment_starts_at_one(self):
        job = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        self.repo.save_box_group(job, self._group(), "작업자1")
        other = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15XYZ")
        saved = self.repo.save_box_group(other, self._group(2), "작업자1")
        self.assertEqual((saved.box_start_no, saved.box_end_no), (1, 2))

    def test_shipment_code_is_normalized_before_matching(self):
        job = self.repo.create_job("작업자1", "V1", "2.2.5", " fba15abc ")
        saved = self.repo.save_box_group(job, self._group(), "작업자1")
        self.assertEqual(saved.box_start_no, 1)
        self.assertEqual(self.repo.next_box_number("FBA15ABC"), 4)

    def test_next_box_number_previews_without_saving(self):
        self.assertEqual(self.repo.next_box_number("FBA15ABC"), 1)
        job = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        self.repo.save_box_group(job, self._group(5), "작업자1")
        self.assertEqual(self.repo.next_box_number("FBA15ABC"), 6)
        self.assertEqual(self.repo.next_box_number("FBA15XYZ"), 1)

    def test_shipment_code_is_exported_and_printed(self):
        job = self.repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        self.repo.save_box_group(job, self._group(), "작업자1")
        self.assertEqual(self.repo.job_rows(job)[0]["shipment_code"], "FBA15ABC")
        group, _items = self.repo.last_group(job)
        self.assertEqual(group["shipment_code"], "FBA15ABC")

    def test_existing_database_without_shipment_column_is_migrated(self):
        path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE packaging_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    product_db_version TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                INSERT INTO packaging_jobs
                VALUES ('old', '2026-01-01', '2026-01-01', '작업자', 'V0', '2.2.3', 'OPEN');
                """
            )
        repo = PackagingRepository(path)
        with sqlite3.connect(path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(packaging_jobs)")}
            kept = conn.execute("SELECT COUNT(*) FROM packaging_jobs").fetchone()[0]
        self.assertIn("shipment_code", columns)
        self.assertEqual(kept, 1)
        job = repo.create_job("작업자1", "V1", "2.2.5", "FBA15ABC")
        self.assertEqual(repo.save_box_group(job, self._group(), "작업자1").box_start_no, 1)

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
