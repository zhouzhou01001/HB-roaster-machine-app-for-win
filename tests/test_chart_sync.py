import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from app.ui.curve_widget import CurveWidget


class ChartSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_unified_chart_uses_one_shared_time_axis(self):
        chart = CurveWidget()
        chart.set_data(
            [
                {"time_s": 0.0, "it": 100.0, "et": 110.0, "bt": 90.0},
                {"time_s": 120.0, "it": 180.0, "et": 190.0, "bt": 170.0},
            ]
        )
        chart.set_actions([{"time_s": 60.0, "gas_kpa": 5.0, "damper_pct": 50.0, "rpm": 300.0}])
        chart.set_time_range(20.0, 100.0)
        self.app.processEvents()
        temperature_view = chart.temp_plot.getViewBox()
        self.assertEqual(temperature_view.viewRange()[0], chart.ror_view.viewRange()[0])
        self.assertIs(chart.gas_curve.getViewBox(), temperature_view)
        self.assertIs(chart.bt_curve.getViewBox(), temperature_view)
        self.assertIs(chart.bt_ror_curve.getViewBox(), chart.ror_view)

    def test_unified_chart_keeps_temperature_and_actions_on_left_axis(self):
        chart = CurveWidget()
        chart.set_data(
            [
                {"time_s": 0.0, "it": 100.0, "et": 110.0, "bt": 90.0, "it_ror": 4.0, "et_ror": 3.0, "bt_ror": 2.0},
                {"time_s": 10.0, "it": 120.0, "et": 130.0, "bt": 105.0, "it_ror": 5.0, "et_ror": 4.0, "bt_ror": 3.0},
            ]
        )
        chart.set_actions([{"time_s": 5.0, "gas_kpa": 5.0, "damper_pct": 50.0, "rpm": 300.0}])

        gas_x, gas_y = chart.gas_curve.getData()
        damper_x, damper_y = chart.damper_curve.getData()
        rpm_x, rpm_y = chart.rpm_curve.getData()
        self.assertEqual(list(gas_x), [5.0])
        self.assertEqual(list(gas_y), [50.0])
        self.assertEqual(list(damper_y), [50.0])
        self.assertEqual(list(rpm_y), [100.0])
        self.assertIn("操作归一化", chart.plot_item.getAxis("left").labelText)
        self.assertEqual(chart.plot_item.getAxis("right").labelText, "升温率 RoR")
        self.assertIs(chart.it_curve.getViewBox(), chart.temp_plot.getViewBox())

    def test_unified_chart_time_range_rejects_invalid_input_and_syncs_view(self):
        chart = CurveWidget()
        chart.set_time_range(20.0, 120.0)
        self.app.processEvents()
        first_range = chart.time_range()
        self.assertAlmostEqual(first_range[0], 20.0, places=3)
        self.assertAlmostEqual(first_range[1], 120.0, places=3)

        with self.assertRaises(ValueError):
            chart.set_time_range(120.0, 20.0)
        self.assertEqual(chart.time_range(), first_range)
        with self.assertRaises(ValueError):
            chart.set_time_range(10.0, 10.0)
        self.assertEqual(chart.time_range(), first_range)

    def test_each_unified_curve_can_toggle_and_change_line_width(self):
        chart = CurveWidget()
        chart.set_curve_visibility("GAS", False)
        chart.set_curve_visibility("BT_ROR", False)
        self.assertFalse(chart.gas_curve.isVisible())
        self.assertFalse(chart.bt_ror_curve.isVisible())
        chart.set_curve_visibility("GAS", True)
        chart.set_curve_visibility("BT_ROR", True)
        self.assertTrue(chart.gas_curve.isVisible())
        self.assertTrue(chart.bt_ror_curve.isVisible())

        chart.set_line_width(1.5)
        self.assertAlmostEqual(chart.gas_curve.opts["pen"].widthF(), 3.0, places=2)
        self.assertAlmostEqual(chart.bt_curve.opts["pen"].widthF(), 4.5, places=2)
        chart.set_curve_line_width("GAS", 0.75)
        self.assertAlmostEqual(chart.curve_line_width("GAS"), 0.75, places=2)
        self.assertAlmostEqual(chart.gas_curve.opts["pen"].widthF(), 1.5, places=2)

    def test_unified_chart_preserves_event_target_and_background_layers(self):
        chart = CurveWidget()
        chart.set_events([{"event_type": "YELLOW", "time_s": 12.0}])
        chart.set_targets(150.0, 195.0)
        chart.set_background_profile(
            [
                {"time_s": 0.0, "bt": 90.0, "bt_ror": 2.0},
                {"time_s": 10.0, "bt": 105.0, "bt_ror": 3.0},
            ],
            "参考曲线",
        )
        self.assertEqual(len(chart._event_markers), 1)
        self.assertEqual(len(chart._target_markers), 2)
        self.assertTrue(chart.bg_bt_curve.isVisible())
        self.assertTrue(chart.bg_ror_curve.isVisible())
        chart.set_curve_visibility("BG_BT", False)
        self.assertFalse(chart.bg_bt_curve.isVisible())
        chart.set_background_profile(
            [{"time_s": 20.0, "bt": 120.0, "bt_ror": 4.0}],
            "新参考曲线",
        )
        self.assertFalse(chart.bg_bt_curve.isVisible())
        self.assertTrue(chart.bg_ror_curve.isVisible())

    def test_mouse_wheel_is_locked_on_plot_and_all_axes(self):
        chart = CurveWidget()
        chart.set_data([
            {"time_s": 0.0, "it": 120.0, "et": 130.0, "bt": 100.0, "bt_ror": 3.0},
            {"time_s": 900.0, "it": 210.0, "et": 220.0, "bt": 190.0, "bt_ror": 8.0},
        ])
        self.app.processEvents()
        before = {
            "x": tuple(chart.plot_item.vb.viewRange()[0]),
            "left": tuple(chart.plot_item.vb.viewRange()[1]),
            "right": tuple(chart.ror_view.viewRange()[1]),
        }

        class WheelEvent:
            def __init__(self):
                self.accepted = False

            def accept(self):
                self.accepted = True

        targets = [
            chart.plot_item.vb,
            chart.ror_view,
            chart.plot_item.getAxis("bottom"),
            chart.plot_item.getAxis("left"),
            chart.plot_item.getAxis("right"),
        ]
        for target in targets:
            event = WheelEvent()
            target.wheelEvent(event)
            self.assertTrue(event.accepted)

        after = {
            "x": tuple(chart.plot_item.vb.viewRange()[0]),
            "left": tuple(chart.plot_item.vb.viewRange()[1]),
            "right": tuple(chart.ror_view.viewRange()[1]),
        }
        self.assertEqual(after, before)

    def test_left_drag_remains_pan_only_and_delegates_to_viewbox(self):
        chart = CurveWidget()

        class DragEvent:
            def __init__(self, button):
                self._button = button
                self.accepted = False

            def button(self):
                return self._button

            def accept(self):
                self.accepted = True

        left_event = DragEvent(QtCore.Qt.MouseButton.LeftButton)
        with patch.object(pg.ViewBox, "mouseDragEvent") as base_drag:
            chart.plot_item.vb.mouseDragEvent(left_event, axis=0)
            base_drag.assert_called_once_with(left_event, axis=0)

        right_event = DragEvent(QtCore.Qt.MouseButton.RightButton)
        with patch.object(pg.ViewBox, "mouseDragEvent") as base_drag:
            chart.plot_item.vb.mouseDragEvent(right_event, axis=0)
            base_drag.assert_not_called()
        self.assertTrue(right_event.accepted)

    def test_auto_follow_extends_x_and_keeps_ticks_readable(self):
        chart = CurveWidget()
        chart.set_data([
            {"time_s": 0.0, "it": 100.0, "et": 110.0, "bt": 90.0, "bt_ror": 2.0},
            {"time_s": 1800.0, "it": 250.0, "et": 260.0, "bt": 230.0, "bt_ror": 9.0},
        ])
        self.app.processEvents()
        snapshot = chart.export_snapshot()
        self.assertEqual(snapshot["x_range"][0], 0.0)
        self.assertGreaterEqual(snapshot["x_range"][1], 1800.0)
        self.assertEqual(snapshot["left_range"], (0.0, 400.0))
        for span, major in (
            (snapshot["x_range"][1] - snapshot["x_range"][0], snapshot["x_major"]),
            (snapshot["right_range"][1] - snapshot["right_range"][0], snapshot["right_major"]),
        ):
            interval_count = span / major
            self.assertGreaterEqual(interval_count, 3.0)
            self.assertLessEqual(interval_count, 12.0)


if __name__ == "__main__":
    unittest.main()
