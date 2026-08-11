import unittest

from beyondpack.labels import render_group_label


class LabelTests(unittest.TestCase):
    def test_label_contains_country_snapshot(self):
        group = {
            "weight_kg": "8.4",
            "length_cm": "42",
            "width_cm": "31",
            "height_cm": "24",
        }
        items = [
            {
                "fnsku": "X1",
                "item_code": "A1",
                "sku": "SKU1",
                "country_code": "DE",
                "country_name": "독일",
                "qty_per_box": 2,
            }
        ]
        label = render_group_label(group, items, 1)
        self.assertIn("독일 (DE)", label)
        self.assertIn("<th>국가</th>", label)

    def test_country_code_only_is_not_duplicated(self):
        group = {
            "weight_kg": "8.4",
            "length_cm": "42",
            "width_cm": "31",
            "height_cm": "24",
        }
        items = [
            {
                "fnsku": "X1",
                "item_code": "A1",
                "sku": "SKU1",
                "country_code": "DE",
                "country_name": "DE",
                "qty_per_box": 2,
            }
        ]
        label = render_group_label(group, items, 1)
        self.assertIn("<td>DE</td>", label)
        self.assertNotIn("DE (DE)", label)


if __name__ == "__main__":
    unittest.main()
