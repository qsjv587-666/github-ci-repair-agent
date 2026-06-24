import unittest

from src.dates import parse_iso_date


class DatesTest(unittest.TestCase):
    def test_parse_iso_date(self):
        self.assertEqual(parse_iso_date("2026-06-24").isoformat(), "2026-06-24")


if __name__ == "__main__":
    unittest.main()
