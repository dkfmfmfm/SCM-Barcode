import tempfile
import unittest
from pathlib import Path

from beyondpack.cache import ProductCacheRepository
from beyondpack.models import Product
from beyondpack.sources.base import ProductBatch, ProductSource
from beyondpack.sync import ProductSyncService


class FakeSource(ProductSource):
    def __init__(self, batch=None, error=None):
        self.batch = batch
        self.error = error

    def fetch_products(self):
        if self.error:
            raise self.error
        return self.batch


def batch(version="V1"):
    return ProductBatch(
        (
            Product(
                "X1", "A1", "SKU1", "US", "미국", "상품1",
                status="Published", data_version=version, schema_version=1,
            ),
        ),
        version,
        1,
    )


class SyncTests(unittest.TestCase):
    def test_failure_uses_valid_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = ProductCacheRepository(root)
            ok = ProductSyncService(FakeSource(batch()), cache, root / "status.json").sync()
            self.assertEqual(ok.state, "CURRENT")
            failed = ProductSyncService(FakeSource(error=OSError("offline")), cache, root / "status.json").sync()
            self.assertEqual(failed.state, "CACHED")
            self.assertEqual(cache.lookup("X1").sku, "SKU1")

    def test_first_sync_failure_blocks_without_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = ProductCacheRepository(root)
            result = ProductSyncService(FakeSource(error=OSError("offline")), cache, root / "status.json").sync()
            self.assertEqual(result.state, "NO_DATA")


if __name__ == "__main__":
    unittest.main()
