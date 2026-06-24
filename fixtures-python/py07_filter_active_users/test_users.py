import unittest

from src.users import active_users


class UsersTest(unittest.TestCase):
    def test_only_active_users_returned(self):
        users = [{"id": 1, "active": True}, {"id": 2, "active": False}]
        self.assertEqual(active_users(users), [{"id": 1, "active": True}])


if __name__ == "__main__":
    unittest.main()
