import unittest

from app.roast.milestone_forecast import predict_milestone_times


class MilestoneForecastTests(unittest.TestCase):
    def test_uses_latest_positive_ror_to_predict_targets(self):
        result = predict_milestone_times([{"time_s": 300, "bt": 140, "bt_ror": 10.0}], 150, 195)
        self.assertEqual(result["yellow_eta_s"], 360.0)
        self.assertEqual(result["fc_eta_s"], 630.0)

    def test_returns_no_eta_when_temperature_is_not_rising(self):
        result = predict_milestone_times([{"time_s": 300, "bt": 140, "bt_ror": 0.0}], 150, 195)
        self.assertIsNone(result["yellow_eta_s"])
        self.assertIsNone(result["fc_eta_s"])


if __name__ == "__main__":
    unittest.main()