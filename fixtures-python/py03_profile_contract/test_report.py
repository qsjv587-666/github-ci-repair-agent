import unittest

from src.report import render_profile


class ReportTest(unittest.TestCase):
    def test_render_profile_uses_service_contract(self):
        profile = {"full_name": "Ada Lovelace"}
        self.assertEqual(render_profile(profile), "User: Ada Lovelace")


if __name__ == "__main__":
    unittest.main()
