import tempfile
import unittest
from pathlib import Path

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
