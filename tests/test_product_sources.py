from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beyondpack.config import GoogleSheetsSettings
from beyondpack.errors import SourceError
from beyondpack.sources.excel_source import ExcelProductSource
from beyondpack.sources.google_sheets import (
    GoogleSheetsProductSource,
    google_sheet_csv_url,
)


HEADERS = (
    "FNSKU,ItemCode,SKU,CountryCode,CountryName,ProductName,ProductNameEn,"
    "AmazonAccount,Status,SourceModifiedAt,DataVersion,SchemaVersion\n"
)
ROWS = (
    "X1,A1,SKU-US,US,미국,상품,,ACCOUNT,Published,,V1,2\n"
    "X1,A1,SKU-DE,DE,독일,상품,,ACCOUNT,Published,,V1,2\n"
)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class GoogleSheetsSourceTests(unittest.TestCase):
    def test_edit_url_becomes_csv_export_url(self):
        result = google_sheet_csv_url(
            "https://docs.google.com/spreadsheets/d/ABC_123/edit?gid=456#gid=456"
        )
        self.assertEqual(
            result,
            "https://docs.google.com/spreadsheets/d/ABC_123/export?format=csv&gid=456",
        )

    def test_csv_is_downloaded_and_mapped_by_country(self):
        payload = (HEADERS + ROWS).encode("utf-8")
        settings = GoogleSheetsSettings(
            spreadsheet_url="https://docs.google.com/spreadsheets/d/ABC/edit?gid=0"
        )
        with patch(
            "beyondpack.sources.google_sheets.urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            batch = GoogleSheetsProductSource(settings).fetch_products()
        self.assertEqual(batch.data_version, "V1")
        self.assertEqual(len(batch.products), 2)
        self.assertEqual(batch.products[0].lookup_key, "X1|US")
        self.assertEqual(batch.products[1].lookup_key, "X1|DE")

    def test_legacy_two_column_sheet_is_rejected(self):
        payload = "X1,상품명\nX2,다른상품\n".encode("utf-8")
        settings = GoogleSheetsSettings(
            spreadsheet_url="https://docs.google.com/spreadsheets/d/ABC/edit?gid=0"
        )
        with patch(
            "beyondpack.sources.google_sheets.urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            with self.assertRaisesRegex(SourceError, "필수 열"):
                GoogleSheetsProductSource(settings).fetch_products()


class ExcelSourceTests(unittest.TestCase):
    def test_excel_emergency_import_uses_same_schema(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "master.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "BeyondPack_Master"
            headers = (HEADERS.strip()).split(",")
            sheet.append(headers)
            sheet.append(ROWS.splitlines()[0].split(","))
            workbook.save(path)
            workbook.close()

            batch = ExcelProductSource(path).fetch_products()
        self.assertEqual(batch.data_version, "V1")
        self.assertEqual(batch.products[0].computed_lookup_key, "X1|US")


if __name__ == "__main__":
    unittest.main()
