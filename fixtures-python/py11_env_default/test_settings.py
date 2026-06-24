import unittest

from src.settings import app_mode


class SettingsTest(unittest.TestCase):
    def test_default_mode_is_dev(self):
        self.assertEqual(app_mode(), "dev")


if __name__ == "__main__":
    unittest.main()
