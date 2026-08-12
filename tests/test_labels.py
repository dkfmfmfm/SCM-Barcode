import unittest

from beyondpack.labels import MAX_ITEM_ROWS, box_numbers, render_group_label


GROUP = {
    "weight_kg": "8.4",
    "length_cm": "42",
    "width_cm": "31",
    "height_cm": "24",
}


def item(fnsku: str = "X1", country_name: str = "독일") -> dict:
    return {
        "fnsku": fnsku,
        "item_code": "A1",
        "sku": "SKU1",
        "country_code": "DE",
        "country_name": country_name,
        "qty_per_box": 2,
    }


class LabelTests(unittest.TestCase):
    def test_label_contains_country_snapshot(self):
        label = render_group_label(GROUP, [item()], 1)
        self.assertIn("독일 (DE)", label)
        self.assertIn("<th>국가</th>", label)

    def test_country_code_only_is_not_duplicated(self):
        label = render_group_label(GROUP, [item(country_name="DE")], 1)
        self.assertIn("<td>DE</td>", label)
        self.assertNotIn("DE (DE)", label)

    def test_box_number_is_printed_with_hash_and_sequence(self):
        label = render_group_label(GROUP, [item()], 3, 8)
        self.assertIn("#3", label)
        self.assertIn("박스 3 / 8", label)

    def test_extra_items_are_summarized_to_keep_one_page_per_box(self):
        items = [item(f"X{index}") for index in range(MAX_ITEM_ROWS + 3)]
        label = render_group_label(GROUP, items, 1, 1)
        self.assertIn("외 3개 품목", label)
        self.assertIn(f"X{MAX_ITEM_ROWS - 1}", label)
        self.assertNotIn(f">X{MAX_ITEM_ROWS}<", label)


class BoxNumberTests(unittest.TestCase):
    def test_box_count_produces_one_number_per_label(self):
        self.assertEqual(box_numbers(1, 8), (1, 2, 3, 4, 5, 6, 7, 8))

    def test_numbers_continue_from_the_saved_start_number(self):
        self.assertEqual(box_numbers(4, 3), (4, 5, 6))

    def test_string_values_from_the_database_are_accepted(self):
        self.assertEqual(box_numbers("7", "2"), (7, 8))

    def test_missing_count_still_prints_one_label(self):
        self.assertEqual(box_numbers(5, 0), (5,))


if __name__ == "__main__":
    unittest.main()
