from __future__ import annotations

import math
import unittest

from app.roast.compare import detect_tp, development_metrics
from app.roast.ror import calculate_ror
from app.roast.sample_quality import SampleQualityGate


class RoastMetricsTests(unittest.TestCase):
    def test_ror_is_available_to_the_application_contract(self) -> None:
        samples = [
            {"time_s": 0, "it": 100, "et": 110, "bt": 90, "work": 120},
            {"time_s": 5, "it": 105, "et": 115, "bt": 95, "work": 125},
            {"time_s": 10, "it": 110, "et": 120, "bt": 100, "work": 130},
        ]
        result = calculate_ror(samples, window=10)
        self.assertAlmostEqual(result["bt_ror"], 60.0)
        self.assertEqual(result["bt_ror"], result["BT_RoR"])
        self.assertAlmostEqual(result["work_ror"], 60.0)
        self.assertEqual(result["work_ror"], result["WORK_RoR"])

    def test_ror_requires_three_unique_points_and_minimum_span(self) -> None:
        self.assertIsNone(calculate_ror([{"time_s": 0, "bt": 100}, {"time_s": 10, "bt": 110}])["bt_ror"])
        short = [{"time_s": 0, "bt": 100}, {"time_s": 1, "bt": 101}, {"time_s": 2, "bt": 102}]
        self.assertIsNone(calculate_ror(short, window=10)["bt_ror"])

    def test_ror_uses_latest_finite_time_and_accepts_out_of_order_samples(self) -> None:
        samples = [
            {"time_s": 10, "it": 10, "et": 10, "bt": 10, "work": 10},
            {"time_s": 0, "it": 0, "et": 0, "bt": 0, "work": 0},
            {"time_s": 5, "it": 5, "et": 5, "bt": 5, "work": 5},
            {"time_s": None, "it": 999, "et": 999, "bt": 999, "work": 999},
        ]
        result = calculate_ror(samples)
        for key in ("it_ror", "et_ror", "bt_ror", "work_ror"):
            self.assertAlmostEqual(result[key], 60.0)

    def test_ror_filters_nonfinite_values_and_keeps_negative_results(self) -> None:
        samples = [
            {"time_s": 0, "bt": 10},
            {"time_s": 5, "bt": 9},
            {"time_s": 7, "bt": float("nan")},
            {"time_s": float("inf"), "bt": 500},
            {"time_s": 10, "bt": 8},
        ]
        result = calculate_ror(samples)
        self.assertAlmostEqual(result["bt_ror"], -12.0)
        self.assertTrue(math.isfinite(result["bt_ror"]))

    def test_duplicate_timestamp_keeps_last_valid_value(self) -> None:
        samples = [
            {"time_s": 0, "bt": 0},
            {"time_s": 5, "bt": 500},
            {"time_s": 5, "bt": 5},
            {"time_s": 10, "bt": 10},
        ]
        self.assertAlmostEqual(calculate_ror(samples)["bt_ror"], 60.0)

    def test_one_second_window_supports_high_frequency_samples(self) -> None:
        samples = [{"time_s": 0.0, "bt": 10.0}, {"time_s": 0.5, "bt": 10.1}, {"time_s": 1.0, "bt": 10.2}]
        self.assertAlmostEqual(calculate_ror(samples, window=1)["bt_ror"], 12.0)

    def test_invalid_ror_windows_are_rejected(self) -> None:
        samples = [{"time_s": 0, "bt": 0}, {"time_s": 5, "bt": 5}, {"time_s": 10, "bt": 10}]
        for window in (0, -1, 61, float("nan"), float("inf"), "bad"):
            with self.subTest(window=window), self.assertRaises(ValueError):
                calculate_ror(samples, window=window)

    def test_turning_point_is_detected_after_recovery(self) -> None:
        samples = [{"time_s": index, "bt": value, "bt_ror": 1.0} for index, value in enumerate([100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100])]
        turning_point = detect_tp(samples, min_width=3, min_recover=2.0)
        self.assertIsNotNone(turning_point)
        self.assertEqual(turning_point["time_s"], 5.0)

    def test_development_metrics_use_first_crack_to_drop(self) -> None:
        events = [{"event_type": "CHARGE", "time_s": 0}, {"event_type": "FC_START", "time_s": 480}, {"event_type": "DROP", "time_s": 600}]
        result = development_metrics(events)
        self.assertEqual(result["development_s"], 120)
        self.assertAlmostEqual(result["development_ratio"], 20.0)
        self.assertTrue(result["complete"])

    def test_quality_gate_rejects_a_temperature_spike(self) -> None:
        gate = SampleQualityGate()
        self.assertTrue(gate.accept({"time_s": 0, "it": 100, "et": 110, "bt": 90}).accepted)
        result = gate.accept({"time_s": 1, "it": 101, "et": 111, "bt": 200})
        self.assertFalse(result.accepted)
        self.assertIn("BT", result.reason)


if __name__ == "__main__":
    unittest.main()
