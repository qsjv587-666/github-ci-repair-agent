import unittest

from src.auth import can_manage_workspace


class AuthTest(unittest.TestCase):
    def test_owner_can_manage_workspace(self):
        self.assertTrue(can_manage_workspace("owner"))


if __name__ == "__main__":
    unittest.main()
