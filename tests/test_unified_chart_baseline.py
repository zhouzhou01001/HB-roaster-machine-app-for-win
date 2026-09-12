import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from app.ui.main_window import MainWindow


SAMPLES = [
    {"time_s": 0.0, "it": 120.0, "et": 128.0, "bt": 92.0, "it_ror": 1.0, "et_ror": 1.2, "bt_ror": 0.8},
    {"time_s": 60.0, "it": 132.0, "et": 142.0, "bt": 104.0, "it_ror": 2.0, "et_ror": 2.4, "bt_ror": 1.8},
    {"time_s": 120.0, "it": 145.0, "et": 158.0, "bt": 116.0, "it_ror": 2.5, "et_ror": 2.8, "bt_ror": 2.1},
    {"time_s": 180.0, "it": 157.0, "et": 172.0, "bt": 130.0, "it_ror": 2.2, "et_ror": 2.3, "bt_ror": 2.0},
]

PREDICTION = {"BT_RoR": [(180.0, 2.0), (210.0, 1.9), (240.0, 1.8)]}

ACTIONS = [
    {"time_s": 30.0, "gas_kpa": 2.0, "damper_pct": 20.0, "rpm": 90.0, "note": "low"},
    {"time_s": 150.0, "gas_kpa": 5.5, "damper_pct": 55.0, "rpm": 180.0, "note": "ramp"},
]

EVENTS = [
    {"event_type": "CHARGE", "time_s": 0.0, "bt": 92.0},
    {"event_type": "YELLOW", "time_s": 120.0, "bt": 116.0},
]

BACKGROUND = [
    {"time_s": 0.0, "bt": 91.0, "bt_ror": 0.7},
    {"time_s": 60.0, "bt": 103.0, "bt_ror": 1.7},
    {"time_s": 120.0, "bt": 115.0, "bt_ror": 2.0},
]


class UnifiedChartBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self._workspace = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._workspace.cleanup)
        self.window = MainWindow(
            db_path=Path(self._workspace.name) / "unified_chart.hbroast",
            channel_mapping_path=Path(self._workspace.name) / "channel.json",
        )
        self.window.resize(1280, 720)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)
        self.chart = self._chart()
        self._set_chart_data()

    def _chart(self):
        for name in ("unified_chart", "chart", "curve"):
            candidate = getattr(self.window, name, None)
            if candidate is not None:
                return candidate
        self.fail("main window does not expose a chart widget")

    def _set_chart_data(self) -> None:
        if hasattr(self.chart, "set_data"):
            self.chart.set_data(SAMPLES, PREDICTION, EVENTS)
        if hasattr(self.chart, "set_actions"):
            self.chart.set_actions(ACTIONS)
        if hasattr(self.chart, "set_targets"):
            self.chart.set_targets(150.0, 195.0)
        if hasattr(self.chart, "set_background_profile"):
            self.chart.set_background_profile(BACKGROUND, "baseline")
        self.app.processEvents()

    def _plot_widget(self):
        return getattr(self.chart, "temp_plot", None) or getattr(self.chart, "plot", None)

    def _plot_item(self):
        return getattr(self.chart, "plot_item", None) or self._plot_widget().getPlotItem()

    def _left_viewbox(self):
        return self._plot_item().vb

    def _right_viewbox(self):
        return getattr(self.chart, "ror_view", None)

    def _curve_item(self, *names):
        for owner in (self.chart,):
            for name in names:
                item = getattr(owner, name, None)
                if item is not None:
                    return item
        self.fail(f"missing curve item: {', '.join(names)}")

    def _curve_data(self, item):
        x_data, y_data = item.getData()
        return (
            [] if x_data is None else list(x_data),
            [] if y_data is None else list(y_data),
        )

    def _manual_range_api(self):
        for name in ("set_manual_x_range", "set_x_range", "apply_x_range", "set_time_range"):
            method = getattr(self.chart, name, None)
            if callable(method):
                return method
        return None

    def _apply_manual_range(self, start, end):
        method = self._manual_range_api()
        if method is not None:
            try:
                return method(start, end)
            except (TypeError, ValueError):
                return False
        self.fail("missing manual X-axis range API/control")

    def _set_curve_visible(self, curve_name: str, visible: bool):
        for method_name in ("set_curve_visibility", "set_curve_visible", "toggle_curve", "set_series_visible"):
            method = getattr(self.chart, method_name, None)
            if callable(method):
                return method(curve_name, visible)
        toggles = getattr(self.chart, "curve_toggles", {})
        toggle = toggles.get(curve_name) if isinstance(toggles, dict) else None
        if toggle is not None:
            toggle.setChecked(bool(visible))
            self.app.processEvents()
            return None
        self.fail("missing independent curve visibility API/control")

    def _set_line_width(self, width: int):
        for method_name in ("set_line_width", "set_curve_line_width", "apply_line_width"):
            method = getattr(self.chart, method_name, None)
            if callable(method):
                return method(width)
        self.fail("missing line width API/control")

    def _chart_control(self, *names):
        for name in names:
            control = self.chart.findChild(QtWidgets.QWidget, name)
            if control is not None:
                return control
            control = self.window.findChild(QtWidgets.QWidget, name)
            if control is not None:
                return control
            control = getattr(self.chart, name, None)
            if control is not None:
                return control
            control = getattr(self.window, name, None)
            if control is not None:
                return control
        return None

    def test_single_chart_contains_temperature_ror_prediction_and_manual_action_curves(self) -> None:
        plot_widget = self._plot_widget()
        self.assertIsNotNone(plot_widget, "unified chart has no plot widget")
        shared_scene = plot_widget.scene()
        for curve_name, item in {
            "BT": self._curve_item("bt_curve"),
            "ET": self._curve_item("et_curve"),
            "IT": self._curve_item("it_curve"),
            "BT RoR": self._curve_item("bt_ror_curve"),
            "ET RoR": self._curve_item("et_ror_curve"),
            "IT RoR": self._curve_item("it_ror_curve"),
            "prediction RoR": self._curve_item("bt_pred_curve", "prediction_ror_curve"),
            "gas": self._curve_item("gas_curve"),
            "damper": self._curve_item("damper_curve"),
            "rpm": self._curve_item("rpm_curve"),
        }.items():
            self.assertIs(item.scene(), shared_scene, f"{curve_name} is not rendered in the unified chart scene")
            x_data, y_data = self._curve_data(item)
            self.assertGreater(len(x_data), 0, f"{curve_name} has no X data")
            self.assertGreater(len(y_data), 0, f"{curve_name} has no Y data")

    def test_axis_binding_contract(self) -> None:
        left_viewbox = self._left_viewbox()
        right_viewbox = self._right_viewbox()
        self.assertIsNotNone(right_viewbox, "unified chart must expose a right-axis ViewBox for RoR")

        for name in ("bt_curve", "et_curve", "it_curve", "gas_curve", "damper_curve", "rpm_curve"):
            item = self._curve_item(name)
            self.assertIs(item.getViewBox(), left_viewbox, f"{name} must be bound to the left axis")

        for name in ("bt_ror_curve", "et_ror_curve", "it_ror_curve", "bt_pred_curve"):
            item = self._curve_item(name)
            self.assertIs(item.getViewBox(), right_viewbox, f"{name} must be bound to the right RoR axis")

    def test_manual_x_axis_range_validation_and_state(self) -> None:
        self._apply_manual_range(30, 150)
        self.app.processEvents()
        view_range = self._plot_widget().getViewBox().viewRange()[0]
        self.assertAlmostEqual(view_range[0], 30.0, places=2)
        self.assertAlmostEqual(view_range[1], 150.0, places=2)

        for invalid_start, invalid_end in ((150, 30), ("", 100), ("abc", 100), (-500, 999999)):
            before = self._plot_widget().getViewBox().viewRange()[0]
            result = self._apply_manual_range(invalid_start, invalid_end)
            self.app.processEvents()
            after = self._plot_widget().getViewBox().viewRange()[0]
            self.assertEqual(after, before, f"invalid X range {invalid_start!r}-{invalid_end!r} changed the active range")
            self.assertIn(result, (False, None), "invalid X range should be rejected or reported without success")

    def test_left_pan_is_temporary_and_new_data_restores_auto_follow(self) -> None:
        if hasattr(self.chart, "set_auto_follow"):
            self.chart.set_auto_follow(True)
        self._plot_widget().setXRange(40, 160, padding=0)
        self.app.processEvents()
        panned = self._plot_widget().getViewBox().viewRange()[0]
        self.assertAlmostEqual(panned[0], 40.0, places=2)
        self.assertAlmostEqual(panned[1], 160.0, places=2)

        extended = SAMPLES + [{"time_s": 900.0, "it": 190.0, "et": 205.0, "bt": 170.0, "it_ror": 1.0, "et_ror": 1.1, "bt_ror": 1.2}]
        if hasattr(self.chart, "set_data"):
            self.chart.set_data(extended, PREDICTION, EVENTS)
        self.app.processEvents()
        after_update = self._plot_widget().getViewBox().viewRange()[0]
        self.assertAlmostEqual(after_update[0], 0.0, places=2)
        self.assertGreaterEqual(after_update[1], 900.0)

    def test_curve_visibility_can_be_toggled_independently(self) -> None:
        bt_curve = self._curve_item("bt_curve")
        et_curve = self._curve_item("et_curve")
        bt_data_before = self._curve_data(bt_curve)
        self._set_curve_visible("BT", False)
        self.assertFalse(bt_curve.isVisible())
        self.assertTrue(et_curve.isVisible())
        self._set_curve_visible("BT", True)
        self.assertTrue(bt_curve.isVisible())
        self.assertEqual(self._curve_data(bt_curve), bt_data_before)

    def test_line_width_updates_immediately_without_mutating_data(self) -> None:
        bt_curve = self._curve_item("bt_curve")
        data_before = self._curve_data(bt_curve)
        scale_before = self.chart.line_width() if hasattr(self.chart, "line_width") else 1.0
        width_before = bt_curve.opts["pen"].widthF()
        self._set_line_width(1.5)
        self.app.processEvents()
        if hasattr(self.chart, "line_width"):
            self.assertAlmostEqual(self.chart.line_width(), 1.5)
        self.assertAlmostEqual(bt_curve.opts["pen"].widthF(), width_before / scale_before * 1.5)
        self.assertEqual(self._curve_data(bt_curve), data_before)

    def test_events_targets_background_and_auto_follow_regression(self) -> None:
        self.assertEqual(len(getattr(self.chart, "_event_markers", [])), len(EVENTS))
        self.assertEqual(len(getattr(self.chart, "_target_markers", [])), 2)
        self.assertTrue(self._curve_item("bg_bt_curve").isVisible())
        self.assertTrue(self._curve_item("bg_ror_curve").isVisible())

        if hasattr(self.chart, "set_auto_follow"):
            self.chart.set_auto_follow(True)
        if hasattr(self.chart, "set_data"):
            self.chart.set_data(SAMPLES + [{"time_s": 900.0, "it": 200.0, "et": 210.0, "bt": 180.0, "it_ror": 1.0, "et_ror": 1.0, "bt_ror": 1.0}], PREDICTION, EVENTS)
        self.app.processEvents()
        followed = self._plot_widget().getViewBox().viewRange()[0]
        self.assertGreaterEqual(followed[1], 900.0)

        if hasattr(self.chart, "set_auto_follow"):
            self.chart.set_auto_follow(False)
        self._plot_widget().setXRange(30, 150, padding=0)
        if hasattr(self.chart, "set_data"):
            self.chart.set_data(SAMPLES + [{"time_s": 1200.0, "it": 210.0, "et": 220.0, "bt": 190.0, "it_ror": 1.0, "et_ror": 1.0, "bt_ror": 1.0}], PREDICTION, EVENTS)
        self.app.processEvents()
        locked = self._plot_widget().getViewBox().viewRange()[0]
        self.assertAlmostEqual(locked[0], 30.0, places=2)
        self.assertAlmostEqual(locked[1], 150.0, places=2)

    def test_chart_controls_are_not_clipped_at_supported_resolutions(self) -> None:
        for width, height in ((1280, 720), (1920, 1080), (3840, 2160)):
            with self.subTest(resolution=f"{width}x{height}"):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertGreaterEqual(self.chart.width(), 540)
                self.assertGreaterEqual(self.chart.height(), 190)
                expected_controls = {
                    "x_start_input": ("x_start_input", "time_start_spin"),
                    "x_end_input": ("x_end_input", "time_end_spin"),
                    "x_range_apply_button": ("x_range_apply_button", "apply_time_range_button"),
                    "line_width_control": ("line_width_control", "line_width_combo"),
                    "curve_visibility_panel": ("curve_visibility_panel", "curve_visibility_button", "curveMenuButton"),
                }
                for control_name, aliases in expected_controls.items():
                    control = self._chart_control(*aliases)
                    self.assertIsNotNone(control, f"missing chart control {control_name}")
                    self.assertTrue(control.isVisible(), f"{control_name} is not visible")
                    self.assertGreater(control.width(), 0, f"{control_name} is clipped horizontally")
                    self.assertGreater(control.height(), 0, f"{control_name} is clipped vertically")


if __name__ == "__main__":
    unittest.main()
