import unittest

from src.pipeline import build_user_label


class PipelineTest(unittest.TestCase):
    def test_pipeline_normalizes_service_data(self):
        self.assertEqual(build_user_label(7), "7:Ada Lovelace")


if __name__ == "__main__":
    unittest.main()
