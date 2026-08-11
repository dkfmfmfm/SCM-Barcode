import tempfile
import unittest
from pathlib import Path

from beyondpack.cache import ProductCacheRepository
from beyondpack.errors import DataValidationError, DuplicateFnskuError, InactiveProductError
from beyondpack.models import Product
from beyondpack.sources.base import ProductBatch


def product(fnsku: str, *, status: str = "Published", version: str = "V1") -> Product:
    return Product(
        fnsku=fnsku,
        item_code="A000000010",
        sku="SKU-1",
        country_code="US",
        country_name="미국",
        product_name="테스트 상품",
        status=status,
        source_modified_at="2026-08-11T00:00:00Z",
        data_version=version,
        schema_version=1,
    )


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = ProductCacheRepository(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_snapshot_and_normalized_lookup(self):
        info = self.cache.replace_snapshot(ProductBatch((product("x003abc123"),), "V1", 1))
        self.assertEqual(info.product_count, 1)
        found = self.cache.lookup(" X003ABC123\r\n")
        self.assertEqual(found.item_code, "A000000010")
        self.assertEqual(self.cache.info().data_version, "V1")

    def test_duplicate_fnsku_rejects_entire_snapshot(self):
        batch = ProductBatch((product("X1"), product(" x1 ")), "V1", 1)
        with self.assertRaises(DuplicateFnskuError):
            self.cache.replace_snapshot(batch)
        self.assertFalse(self.cache.exists())

    def test_missing_required_value_preserves_previous_database(self):
        self.cache.replace_snapshot(ProductBatch((product("X1"),), "V1", 1))
        invalid = Product(**{**product("X2", version="V2").to_dict(), "sku": ""})
        with self.assertRaises(DataValidationError):
            self.cache.replace_snapshot(ProductBatch((invalid,), "V2", 1))
        self.assertEqual(self.cache.lookup("X1").data_version, "V1")

    def test_large_drop_is_rejected(self):
        original = tuple(product(f"X{i}") for i in range(10))
        self.cache.replace_snapshot(ProductBatch(original, "V1", 1))
        reduced = tuple(product(f"Y{i}", version="V2") for i in range(7))
        with self.assertRaises(DataValidationError):
            self.cache.replace_snapshot(ProductBatch(reduced, "V2", 1), 0.20)
        self.assertEqual(self.cache.info().product_count, 10)

    def test_inactive_product_has_distinct_error(self):
        batch = ProductBatch((product("X1"), product("X2", status="Inactive")), "V1", 1)
        self.cache.replace_snapshot(batch)
        with self.assertRaises(InactiveProductError):
            self.cache.lookup("X2")


if __name__ == "__main__":
    unittest.main()

