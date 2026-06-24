import unittest

from src.consumer import normalize_signup_date


class ConsumerTest(unittest.TestCase):
    def test_normalize_signup_date(self):
        self.assertEqual(normalize_signup_date("2026-06-24"), "2026-06-24")


if __name__ == "__main__":
    unittest.main()
