from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.roast.configuration import (
    HardwareProtocol,
    RoastConfiguration,
    RoastConfigurationState,
    Theme,
)
from app.ui.roast_configuration_dialog import RoastConfigurationDialog


class RoastConfigurationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.dialogs: list[RoastConfigurationDialog] = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def make_dialog(self, state: RoastConfigurationState) -> RoastConfigurationDialog:
        dialog = RoastConfigurationDialog(state)
        self.dialogs.append(dialog)
        return dialog

    def test_builds_six_themable_sections_with_documented_ranges(self) -> None:
        dialog = self.make_dialog(RoastConfigurationState())

        self.assertEqual(dialog.objectName(), "roastConfigurationDialog")
        self.assertEqual(dialog.property("uiRole"), "dialog")
        section_names = (
            "acquisitionSection",
            "rorSection",
            "autoTpSection",
            "protocolSection",
            "themeSection",
            "retentionSection",
        )
        for name in section_names:
            section = dialog.findChild(QtWidgets.QGroupBox, name)
            self.assertIsNotNone(section)
            self.assertEqual(section.property("uiRole"), "formPanel")

        self.assertEqual(
            tuple(dialog.sample_rate_combo.itemData(index) for index in range(dialog.sample_rate_combo.count())),
            (1, 2, 4, 5, 10),
        )
        self.assertEqual((dialog.ror_window_spin.minimum(), dialog.ror_window_spin.maximum()), (1, 60))
        self.assertFalse(dialog.auto_tp_checkbox.isChecked())
        self.assertEqual(
            tuple(dialog.hardware_protocol_combo.itemText(index) for index in range(4)),
            ("HB Model S", "HB TC4", "HB Legacy 3", "模拟"),
        )
        self.assertEqual((dialog.retention_spin.minimum(), dialog.retention_spin.maximum()), (30, 365))
        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertEqual(dialog.findChild(QtWidgets.QLabel, "configurationStatusBadge").property("uiRole"), "statusBadge")
        self.assertEqual(dialog.preview_panel.property("uiRole"), "panelSecondary")
        self.assertEqual(dialog.findChild(QtWidgets.QFrame, "configurationActions").property("uiRole"), "dialogActions")
        self.assertFalse(
            any(label.text().startswith("原：") for label in dialog.findChildren(QtWidgets.QLabel))
        )

    def test_form_reflows_without_changing_available_values(self) -> None:
        dialog = self.make_dialog(RoastConfigurationState())
        rates = tuple(
            dialog.sample_rate_combo.itemData(index)
            for index in range(dialog.sample_rate_combo.count())
        )

        dialog.resize(660, 620)
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.property("layoutMode"), "stacked")

        dialog.resize(780, 680)
        self.app.processEvents()
        self.assertEqual(dialog.property("layoutMode"), "grid")
        self.assertEqual(rates, (1, 2, 4, 5, 10))

    def test_opening_editing_and_preview_have_no_external_side_effects(self) -> None:
        state = RoastConfigurationState()
        current_before = state.current
        pending_before = state.pending

        with patch("builtins.open", side_effect=AssertionError("dialog must not perform file IO")):
            dialog = self.make_dialog(state)
            dialog.sample_rate_combo.setCurrentIndex(dialog.sample_rate_combo.findData(5))
            dialog.ror_window_spin.setValue(18)
            dialog.hardware_protocol_combo.setCurrentIndex(
                dialog.hardware_protocol_combo.findData(HardwareProtocol.HB_TC4)
            )
            dialog.preview_button.click()
            self.app.processEvents()

        self.assertIs(state.current, current_before)
        self.assertIs(state.pending, pending_before)
        self.assertEqual(dialog.preview_table.rowCount(), 3)
        self.assertTrue(dialog.confirm_button.isEnabled())

    def test_confirmation_emits_exact_detached_payload_and_diff(self) -> None:
        state = RoastConfigurationState()
        dialog = self.make_dialog(state)
        emitted: list[dict] = []
        dialog.confirm_requested.connect(emitted.append)

        dialog.sample_rate_combo.setCurrentIndex(dialog.sample_rate_combo.findData(10))
        dialog.ror_window_spin.setValue(20)
        dialog.auto_tp_checkbox.setChecked(True)
        dialog.hardware_protocol_combo.setCurrentIndex(
            dialog.hardware_protocol_combo.findData(HardwareProtocol.SIMULATION)
        )
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData(Theme.LIGHT))
        dialog.retention_spin.setValue(180)
        dialog.preview_button.click()
        self.app.processEvents()
        dialog.confirm_button.click()
        self.app.processEvents()

        self.assertEqual(len(emitted), 1)
        payload = emitted[0]
        self.assertEqual(payload["apply_at"], "next_batch")
        self.assertEqual(
            payload["configuration"],
            {
                "sample_rate_hz": 10,
                "ror_window_seconds": 20,
                "theme": "light",
                "auto_tp_enabled": True,
                "data_retention_days": 180,
                "hardware_protocol": "SIMULATION",
            },
        )
        self.assertEqual(
            [item["field"] for item in payload["diff"]],
            [
                "sample_rate_hz",
                "ror_window_seconds",
                "theme",
                "auto_tp_enabled",
                "data_retention_days",
                "hardware_protocol",
            ],
        )
        self.assertEqual(payload["diff"][0]["effective_timing"], "next_batch")
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertIsNone(state.pending)
        self.assertEqual(state.current, RoastConfiguration())

        payload["configuration"]["sample_rate_hz"] = 1
        self.assertEqual(dialog.preview_payload()["configuration"]["sample_rate_hz"], 10)

    def test_edit_after_preview_invalidates_confirmation(self) -> None:
        dialog = self.make_dialog(RoastConfigurationState())
        dialog.sample_rate_combo.setCurrentIndex(dialog.sample_rate_combo.findData(2))
        dialog.preview_button.click()
        self.assertTrue(dialog.confirm_button.isEnabled())

        dialog.ror_window_spin.setValue(12)

        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertIsNone(dialog.preview_payload())
        self.assertEqual(dialog.preview_table.rowCount(), 0)

    def test_cancel_does_not_change_current_or_existing_pending_state(self) -> None:
        state = RoastConfigurationState()
        existing_pending = state.stage(sample_rate_hz=4, theme=Theme.LIGHT)
        current_before = state.current
        dialog = self.make_dialog(state)
        self.assertEqual(dialog.sample_rate_combo.currentData(), 4)
        self.assertEqual(dialog.theme_combo.currentData(), Theme.LIGHT.value)

        dialog.retention_spin.setValue(240)
        dialog.reject()
        self.app.processEvents()

        self.assertIs(state.current, current_before)
        self.assertIs(state.pending, existing_pending)
        self.assertEqual(state.pending.data_retention_days, 90)

    def test_unchanged_configuration_cannot_be_confirmed(self) -> None:
        dialog = self.make_dialog(RoastConfigurationState())
        emitted: list[dict] = []
        dialog.confirm_requested.connect(emitted.append)

        dialog.preview_button.click()
        dialog.confirm_button.click()

        self.assertEqual(dialog.preview_table.rowCount(), 0)
        self.assertFalse(dialog.confirm_button.isEnabled())
        self.assertEqual(emitted, [])


if __name__ == "__main__":
    unittest.main()
