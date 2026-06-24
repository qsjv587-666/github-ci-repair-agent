import unittest

from src.orders import grand_total


class OrdersTest(unittest.TestCase):
    def test_total_includes_tax(self):
        orders = [{"subtotal": 10, "tax": 1}, {"subtotal": 5, "tax": 0.5}]
        self.assertEqual(grand_total(orders), 16.5)


if __name__ == "__main__":
    unittest.main()
