from __future__ import annotations

import unittest

from app.roast.configuration import (
    EffectiveTiming,
    HardwareProtocol,
    RoastConfiguration,
    RoastConfigurationState,
    Theme,
)


class RoastConfigurationTests(unittest.TestCase):
    def test_defaults_are_typed_and_match_the_operation_manual(self) -> None:
        configuration = RoastConfiguration()

        self.assertEqual(configuration.sample_rate_hz, 1)
        self.assertEqual(configuration.ror_window_seconds, 10)
        self.assertIs(configuration.theme, Theme.DARK)
        self.assertFalse(configuration.auto_tp_enabled)
        self.assertEqual(configuration.data_retention_days, 90)
        self.assertIs(configuration.hardware_protocol, HardwareProtocol.HB_MODEL_S)

    def test_all_documented_sample_rates_are_valid(self) -> None:
        for sample_rate in (1, 2, 4, 5, 10):
            with self.subTest(sample_rate=sample_rate):
                self.assertEqual(
                    RoastConfiguration(sample_rate_hz=sample_rate).sample_rate_hz,
                    sample_rate,
                )

    def test_enum_values_are_normalized_to_typed_members(self) -> None:
        configuration = RoastConfiguration(
            theme="light",  # type: ignore[arg-type]
            hardware_protocol="SIMULATION",  # type: ignore[arg-type]
        )

        self.assertIs(configuration.theme, Theme.LIGHT)
        self.assertIs(configuration.hardware_protocol, HardwareProtocol.SIMULATION)

    def test_invalid_values_are_rejected(self) -> None:
        invalid_cases = (
            ({"sample_rate_hz": 3}, ValueError),
            ({"sample_rate_hz": True}, TypeError),
            ({"ror_window_seconds": 0}, ValueError),
            ({"ror_window_seconds": 61}, ValueError),
            ({"ror_window_seconds": 1.5}, TypeError),
            ({"auto_tp_enabled": 1}, TypeError),
            ({"data_retention_days": 29}, ValueError),
            ({"data_retention_days": 366}, ValueError),
            ({"theme": "sepia"}, ValueError),
            ({"hardware_protocol": "UNKNOWN"}, ValueError),
        )

        for values, error_type in invalid_cases:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    RoastConfiguration(**values)  # type: ignore[arg-type]

    def test_stage_keeps_current_separate_and_accumulates_pending_changes(self) -> None:
        state = RoastConfigurationState()
        original = state.current

        first_pending = state.stage(sample_rate_hz=5)
        final_pending = state.stage(theme=Theme.LIGHT, auto_tp_enabled=True)

        self.assertIs(state.current, original)
        self.assertEqual(first_pending.sample_rate_hz, 5)
        self.assertEqual(final_pending.sample_rate_hz, 5)
        self.assertIs(final_pending.theme, Theme.LIGHT)
        self.assertTrue(final_pending.auto_tp_enabled)
        self.assertIs(state.pending, final_pending)
        self.assertTrue(state.has_pending)

    def test_preview_is_ordered_complete_and_has_no_side_effects(self) -> None:
        state = RoastConfigurationState()
        state.stage(
            sample_rate_hz=4,
            ror_window_seconds=15,
            theme=Theme.LIGHT,
            auto_tp_enabled=True,
            data_retention_days=180,
            hardware_protocol=HardwareProtocol.HB_TC4,
        )
        current_before = state.current
        pending_before = state.pending

        preview = state.preview()
        preview_again = state.preview()

        self.assertEqual(
            [difference.field for difference in preview],
            [
                "sample_rate_hz",
                "ror_window_seconds",
                "theme",
                "auto_tp_enabled",
                "data_retention_days",
                "hardware_protocol",
            ],
        )
        self.assertEqual(
            [difference.effective_timing for difference in preview],
            [
                EffectiveTiming.NEXT_BATCH,
                EffectiveTiming.IMMEDIATE,
                EffectiveTiming.IMMEDIATE,
                EffectiveTiming.IMMEDIATE,
                EffectiveTiming.NEXT_CLEANUP,
                EffectiveTiming.ON_RECONNECT,
            ],
        )
        self.assertEqual(preview, preview_again)
        self.assertIs(state.current, current_before)
        self.assertIs(state.pending, pending_before)

    def test_preview_data_is_detached_from_configuration_state(self) -> None:
        state = RoastConfigurationState()
        state.stage(theme=Theme.LIGHT)

        preview_data = state.preview_data()
        preview_data[0]["pending"] = "tampered"

        self.assertIs(state.pending.theme, Theme.LIGHT)  # type: ignore[union-attr]
        self.assertEqual(state.preview_data()[0]["pending"], "light")

    def test_start_next_batch_atomically_promotes_and_clears_pending(self) -> None:
        state = RoastConfigurationState()
        pending = state.stage(sample_rate_hz=10, data_retention_days=365)

        active = state.start_next_batch()

        self.assertIs(active, pending)
        self.assertIs(state.current, pending)
        self.assertIsNone(state.pending)
        self.assertFalse(state.has_pending)
        self.assertEqual(state.start_next_batch(), active)

    def test_failed_stage_preserves_existing_state(self) -> None:
        state = RoastConfigurationState()
        pending = state.stage(sample_rate_hz=2)
        current = state.current

        with self.assertRaises(ValueError):
            state.stage(data_retention_days=0)

        self.assertIs(state.current, current)
        self.assertIs(state.pending, pending)

        with self.assertRaises(ValueError):
            state.stage(unsupported_option=True)

        self.assertIs(state.pending, pending)

    def test_reverting_to_current_or_discarding_clears_pending(self) -> None:
        state = RoastConfigurationState()
        state.stage(sample_rate_hz=2)
        state.stage(sample_rate_hz=1)
        self.assertIsNone(state.pending)

        state.stage(theme=Theme.LIGHT)
        state.discard_pending()
        self.assertIsNone(state.pending)
        self.assertEqual(state.preview(), ())


if __name__ == "__main__":
    unittest.main()
