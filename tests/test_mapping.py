import unittest

from beyondpack.sources.mapping import map_product


class MappingTests(unittest.TestCase):
    def test_sharepoint_lookup_key_is_mapped(self):
        product = map_product(
            {
                "FNSKU": " x1 ",
                "ItemCode": "A1",
                "SKU": "SKU1",
                "CountryCode": "de",
                "CountryName": "독일",
                "ProductName": "상품",
                "DataVersion": "V2",
                "SchemaVersion": 2,
                "LookupKey": "X1|DE",
            },
            "V2",
            2,
        )
        self.assertEqual(product.computed_lookup_key, "X1|DE")
        self.assertEqual(product.lookup_key, "X1|DE")


if __name__ == "__main__":
    unittest.main()
