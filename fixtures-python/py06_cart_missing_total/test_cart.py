import unittest

from src.cart import line_total


class CartTest(unittest.TestCase):
    def test_missing_total_defaults_to_zero(self):
        self.assertEqual(line_total({"sku": "A-1"}), 0)


if __name__ == "__main__":
    unittest.main()
