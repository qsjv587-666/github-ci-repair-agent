import unittest

from src.formatter import display_name


class FormatterTest(unittest.TestCase):
    def test_none_name_is_empty(self):
        self.assertEqual(display_name(None), "")


if __name__ == "__main__":
    unittest.main()
