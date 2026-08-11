import unittest
from decimal import Decimal

from beyondpack.errors import PackagingValidationError
from beyondpack.normalization import normalize_fnsku, positive_decimal, positive_int


class NormalizationTests(unittest.TestCase):
    def test_fnsku_removes_scanner_whitespace_and_uppercases(self):
        self.assertEqual(normalize_fnsku("  x00 3\tAbc\r\n123 "), "X003ABC123")

    def test_positive_integer_rejects_decimal_and_zero(self):
        for value in ("1.5", 0, -1, "abc"):
            with self.subTest(value=value), self.assertRaises(PackagingValidationError):
                positive_int(value, "수량")

    def test_positive_decimal_rejects_non_finite_and_max(self):
        self.assertEqual(positive_decimal("8.40", "무게"), Decimal("8.40"))
        for value in ("NaN", "Infinity", 0, -1, 501):
            with self.subTest(value=value), self.assertRaises(PackagingValidationError):
                positive_decimal(value, "치수", Decimal("500"))


if __name__ == "__main__":
    unittest.main()

