import unittest

from src.pagination import page_items


class PaginationTest(unittest.TestCase):
    def test_first_page_starts_at_zero(self):
        self.assertEqual(page_items([1, 2, 3, 4], page=1, size=2), [1, 2])


if __name__ == "__main__":
    unittest.main()
