from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from app.ui.curve_widget import CurveWidget


class NonnegativeAxesAndExhaustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.chart = CurveWidget()
        self.addCleanup(self.chart.close)

    def test_exhaust_temperature_is_plotted_from_work_or_ch4(self) -> None:
        self.chart.set_data(
            [
                {"time_s": 0.0, "bt": 100.0, "work": 121.0},
                {"time_s": 1.0, "bt": 101.0, "CH4": 122.0},
            ]
        )
        x_data, y_data = self.chart.exhaust_curve.getData()
        self.assertEqual(list(x_data), [0.0, 1.0])
        self.assertEqual(list(y_data), [121.0, 122.0])
        self.assertIn("EXHAUST", self.chart._curve_items)

    def test_both_y_axes_are_permanently_clamped_to_zero(self) -> None:
        self.chart.set_axis_range("left_y", -50.0, 400.0, 50.0)
        self.chart.set_axis_range("right_y", -20.0, 20.0, 5.0)
        self.assertGreaterEqual(self.chart.plot_item.vb.viewRange()[1][0], 0.0)
        self.assertGreaterEqual(self.chart.ror_view.viewRange()[1][0], 0.0)

        self.chart.plot_item.vb.setYRange(-100.0, 100.0, padding=0)
        self.chart.ror_view.setYRange(-100.0, 20.0, padding=0)
        self.assertGreaterEqual(self.chart.plot_item.vb.viewRange()[1][0], 0.0)
        self.assertGreaterEqual(self.chart.ror_view.viewRange()[1][0], 0.0)

    def test_auto_fit_cannot_restore_negative_ror_ticks(self) -> None:
        self.chart.set_data(
            [
                {"time_s": 0.0, "bt": 100.0, "bt_ror": -8.0, "work": 110.0},
                {"time_s": 1.0, "bt": 101.0, "bt_ror": 4.0, "work": 111.0},
            ]
        )
        self.chart._axis_auto["right"] = True
        self.chart._fit_y_ranges()
        self.assertGreaterEqual(self.chart.ror_view.viewRange()[1][0], 0.0)


if __name__ == "__main__":
    unittest.main()
