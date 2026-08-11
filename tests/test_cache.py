import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from beyondpack.cache import ProductCacheRepository
from beyondpack.errors import DataValidationError, DuplicateProductKeyError, InactiveProductError, ProductNotFoundError
from beyondpack.models import Product
from beyondpack.sources.base import ProductBatch


def product(
    fnsku: str,
    *,
    country_code: str = "US",
    country_name: str = "미국",
    status: str = "Published",
    version: str = "V1",
) -> Product:
    normalized = "".join(fnsku.split()).upper()
    return Product(
        fnsku=fnsku,
        item_code="A000000010",
        sku="SKU-1",
        country_code=country_code,
        country_name=country_name,
        product_name="테스트 상품",
        status=status,
        source_modified_at="2026-08-11T00:00:00Z",
        data_version=version,
        schema_version=2,
        lookup_key=f"{normalized}|{country_code}",
    )


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = ProductCacheRepository(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_snapshot_and_normalized_lookup(self):
        info = self.cache.replace_snapshot(ProductBatch((product("x003abc123"),), "V1", 2))
        self.assertEqual(info.product_count, 1)
        found = self.cache.lookup(" X003ABC123\r\n", "us")
        self.assertEqual(found.item_code, "A000000010")
        self.assertEqual(self.cache.info().data_version, "V1")

    def test_staging_database_names_are_unique(self):
        first = self.cache._new_snapshot_path()
        second = self.cache._new_snapshot_path()
        self.assertNotEqual(first, second)
        self.assertTrue(first.name.endswith(".new.db"))

    def test_windows_file_lock_is_retried(self):
        source = Path(self.temp.name) / "source.db"
        target = Path(self.temp.name) / "target.db"
        with patch(
            "beyondpack.cache.os.replace",
            side_effect=[PermissionError("locked"), None],
        ) as replace:
            self.cache._replace_with_retry(source, target)
        self.assertEqual(replace.call_count, 2)

    def test_read_connections_are_closed_before_snapshot_replacement(self):
        self.cache.replace_snapshot(ProductBatch((product("X1"),), "V1", 2))
        real_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch("beyondpack.cache.sqlite3.connect", side_effect=tracked_connect):
            self.cache.info()
            self.cache.available_countries()
            self.cache.lookup("X1", "US")

        self.assertEqual(len(opened), 3)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

        self.cache.replace_snapshot(ProductBatch((product("X2", version="V2"),), "V2", 2))
        self.assertEqual(self.cache.lookup("X2", "US").data_version, "V2")

    def test_duplicate_composite_key_rejects_entire_snapshot(self):
        batch = ProductBatch((product("X1"), product(" x1 ")), "V1", 2)
        with self.assertRaises(DuplicateProductKeyError):
            self.cache.replace_snapshot(batch)
        self.assertFalse(self.cache.exists())

    def test_same_fnsku_in_different_countries_is_valid(self):
        batch = ProductBatch(
            (
                product("X1", country_code="US", country_name="미국"),
                product("X1", country_code="DE", country_name="독일"),
            ),
            "V1",
            2,
        )
        self.cache.replace_snapshot(batch)
        self.assertEqual(self.cache.lookup("X1", "US").country_name, "미국")
        self.assertEqual(self.cache.lookup("X1", "DE").country_name, "독일")
        self.assertEqual(self.cache.available_countries(), (("DE", "독일"), ("US", "미국")))

    def test_wrong_country_lists_available_country(self):
        self.cache.replace_snapshot(ProductBatch((product("X1"),), "V1", 2))
        with self.assertRaisesRegex(ProductNotFoundError, "등록 국가: US"):
            self.cache.lookup("X1", "DE")

    def test_schema1_cache_remains_readable_during_offline_upgrade(self):
        self.cache.data_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.cache.db_path)) as conn:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE products (
                        fnsku TEXT NOT NULL,
                        normalized_fnsku TEXT NOT NULL UNIQUE,
                        item_code TEXT NOT NULL,
                        sku TEXT NOT NULL,
                        country_code TEXT NOT NULL,
                        country_name TEXT NOT NULL,
                        product_name TEXT NOT NULL,
                        product_name_en TEXT NOT NULL DEFAULT '',
                        amazon_account TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        source_modified_at TEXT NOT NULL DEFAULT '',
                        data_version TEXT NOT NULL,
                        schema_version INTEGER NOT NULL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "X1", "X1", "A1", "SKU1", "US", "미국", "상품1",
                        "", "", "Published", "", "V1", 1,
                    ),
                )
                conn.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    (
                        ("data_version", "V1"),
                        ("schema_version", "1"),
                        ("synced_at", "2026-08-11T00:00:00+00:00"),
                    ),
                )
        self.assertEqual(self.cache.lookup("X1", "US").sku, "SKU1")

    def test_missing_required_value_preserves_previous_database(self):
        self.cache.replace_snapshot(ProductBatch((product("X1"),), "V1", 2))
        invalid = Product(**{**product("X2", version="V2").to_dict(), "sku": ""})
        with self.assertRaises(DataValidationError):
            self.cache.replace_snapshot(ProductBatch((invalid,), "V2", 2))
        self.assertEqual(self.cache.lookup("X1", "US").data_version, "V1")

    def test_large_drop_is_rejected(self):
        original = tuple(product(f"X{i}") for i in range(10))
        self.cache.replace_snapshot(ProductBatch(original, "V1", 2))
        reduced = tuple(product(f"Y{i}", version="V2") for i in range(7))
        with self.assertRaises(DataValidationError):
            self.cache.replace_snapshot(ProductBatch(reduced, "V2", 2), 0.20)
        self.assertEqual(self.cache.info().product_count, 10)

    def test_inactive_product_has_distinct_error(self):
        batch = ProductBatch((product("X1"), product("X2", status="Inactive")), "V1", 2)
        self.cache.replace_snapshot(batch)
        with self.assertRaises(InactiveProductError):
            self.cache.lookup("X2", "US")


if __name__ == "__main__":
    unittest.main()
