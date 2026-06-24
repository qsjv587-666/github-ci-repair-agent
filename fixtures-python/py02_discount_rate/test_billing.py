import unittest

from src.billing import enterprise_discount


class BillingTest(unittest.TestCase):
    def test_enterprise_discount_uses_contract_rate(self):
        self.assertEqual(enterprise_discount(100), 80)


if __name__ == "__main__":
    unittest.main()
