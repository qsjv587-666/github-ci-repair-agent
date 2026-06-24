import unittest

from src.serializer import User, serialize_user


class SerializerTest(unittest.TestCase):
    def test_uses_public_full_name_field(self):
        self.assertEqual(serialize_user(User(1, "Grace Hopper")), {"id": 1, "full_name": "Grace Hopper"})


if __name__ == "__main__":
    unittest.main()
