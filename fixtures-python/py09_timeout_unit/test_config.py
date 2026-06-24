import unittest

from src.config import timeout_millis


class ConfigTest(unittest.TestCase):
    def test_seconds_are_converted_to_millis(self):
        self.assertEqual(timeout_millis({"timeout_seconds": 3}), 3000)


if __name__ == "__main__":
    unittest.main()
