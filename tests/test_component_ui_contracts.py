import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.ui.action_panel import ActionPanel
from app.ui.curve_widget import CURVE_COLORS, CurveWidget
from app.ui.event_panel import EVENT_BUTTONS, EventPanel


class ComponentUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_temperature_ror_pairs_share_color_and_use_solid_dash_hierarchy(self) -> None:
        chart = CurveWidget()
        pairs = (
            (chart.bt_curve, chart.bt_ror_curve, CURVE_COLORS["bt"]),
            (chart.et_curve, chart.et_ror_curve, CURVE_COLORS["et"]),
            (chart.it_curve, chart.it_ror_curve, CURVE_COLORS["inlet"]),
            (chart.exhaust_curve, chart.exhaust_ror_curve, CURVE_COLORS["exhaust"]),
        )
        for temperature, ror, expected_color in pairs:
            self.assertEqual(temperature.opts["pen"].color().name(), expected_color)
            self.assertEqual(ror.opts["pen"].color().name(), expected_color)
            self.assertEqual(temperature.opts["pen"].style(), QtCore.Qt.PenStyle.SolidLine)
            self.assertEqual(ror.opts["pen"].style(), QtCore.Qt.PenStyle.DashLine)

        self.assertEqual(chart.gas_curve.opts["pen"].color().name(), CURVE_COLORS["gas"])
        self.assertEqual(chart.damper_curve.opts["pen"].color().name(), CURVE_COLORS["damper"])
        self.assertEqual(chart.rpm_curve.opts["pen"].color().name(), CURVE_COLORS["rpm"])
        self.assertEqual(chart.exhaust_curve.opts["pen"].color().name(), CURVE_COLORS["exhaust"])
        self.assertEqual(chart.exhaust_curve.opts["pen"].style(), QtCore.Qt.PenStyle.SolidLine)
        self.assertEqual(len(chart.curve_labels()), 14)
        self.assertEqual(chart.curve_labels()["EXHAUST"], "排风温 XT")
        self.assertIn("工艺参数", chart.plot_item.getAxis("left").labelText)
        self.assertIn("温度 / RoR", chart.temperature_lane_label.toPlainText())
        self.assertIn("风门/转速 0–10", chart.process_lane_label.toPlainText())

    def test_event_buttons_keep_codes_in_four_by_two_visual_hierarchy(self) -> None:
        panel = EventPanel()
        expected_main = dict(EVENT_BUTTONS)
        positions = [panel._grid.getItemPosition(index) for index in range(panel._grid.count())]
        self.assertEqual({row for row, _column, _row_span, _column_span in positions}, {0, 1})
        self.assertEqual({column for _row, column, _row_span, _column_span in positions}, {0, 1, 2, 3})
        for event_code, button in panel._buttons.items():
            self.assertIsInstance(button, QtWidgets.QPushButton)
            self.assertEqual(button.main_text, expected_main[event_code])
            self.assertEqual(button.event_code, event_code)
            self.assertEqual(button.property("eventState"), "pending")

        panel.set_session_active(True)
        panel.set_events(
            [
                {"event_type": "YELLOW", "time_s": 100.0},
                {"event_type": "DROP", "time_s": 200.0},
            ]
        )
        self.assertEqual(panel._buttons["YELLOW"].property("eventState"), "recorded")
        self.assertEqual(panel._buttons["DROP"].property("eventState"), "locked")
        self.assertFalse(panel._buttons["DROP"].isEnabled())

        panel.set_compact_mode(True)
        for button in panel._buttons.values():
            self.assertGreaterEqual(button.minimumHeight(), 34)
            self.assertGreaterEqual(button.minimumHeight(), button.sizeHint().height())

    def test_action_controls_keep_slider_and_exact_step_buttons_in_compact_mode(self) -> None:
        panel = ActionPanel()
        emitted = []
        panel.action_added.connect(lambda *values: emitted.append(values))
        for card in panel._control_cards:
            self.assertEqual(card._decrement_button.size(), QtCore.QSize(18, 18))
            self.assertEqual(card._increment_button.size(), QtCore.QSize(18, 18))
            self.assertFalse(card._slider.isHidden())

        panel._control_cards[0]._increment_button.click()
        self.assertEqual(panel.gas_spin.value(), 0.5)
        panel._control_cards[0]._decrement_button.click()
        self.assertEqual(panel.gas_spin.value(), 0.0)
        self.assertEqual(emitted, [])
        self.assertEqual((panel.damper_spin.minimum(), panel.damper_spin.maximum()), (0.0, 10.0))
        self.assertEqual((panel.rpm_spin.minimum(), panel.rpm_spin.maximum()), (0.0, 10.0))

        panel.set_compact_mode(True)
        for card in panel._control_cards:
            self.assertFalse(card._slider.isHidden())
            self.assertEqual(card.layout().indexOf(card._slider), 2)
            self.assertGreaterEqual(card._slider.minimumWidth(), 180)
            self.assertTrue(card._meta_label.isHidden())
            self.assertTrue(card._scale.isHidden())
        self.assertTrue(panel.note_edit.isHidden())


if __name__ == "__main__":
    unittest.main()
