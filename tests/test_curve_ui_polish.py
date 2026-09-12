import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

from app.ui.curve_widget import CurveWidget
from app.ui.main_window import MainWindow


class CurveUiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_both_themes_keep_plot_title_hidden_and_units_explicit(self):
        chart = CurveWidget()
        self.addCleanup(chart.close)
        for dark in (False, True):
            chart.set_theme(dark)
            self.assertFalse(chart.plot_item.titleLabel.isVisible())
            self.assertIn("操作归一化", chart.plot_item.getAxis("left").labelText)

    def test_font_changes_do_not_restore_minor_tick_numbers(self):
        chart = CurveWidget()
        self.addCleanup(chart.close)
        for size in (10, 12, 22):
            chart.apply_view_settings(size, 1.0)
            for name in ("bottom", "left", "right"):
                axis = chart.plot_item.getAxis(name)
                self.assertEqual(axis.style["maxTextLevel"], 0)
                self.assertTrue(axis.style["hideOverlappingLabels"])

    def test_curve_menu_and_compact_toolbar(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow(Path(root) / "polish.hbroast", Path(root) / "channel.json")
            try:
                window.resize(1280, 720)
                window.show()
                self.app.processEvents()
                self.assertFalse(window.forecast_yellow_chip.isVisible())
                self.assertFalse(window.forecast_fc_chip.isVisible())
                self.assertEqual(len(window.curve_visibility_actions), 14)
                self.assertEqual(window.lcd_band.width(), window._ui_value(180))
                for index in range(6):
                    self.assertEqual(window.lcd_metrics_layout.getItemPosition(index)[:2], (index, 0))
                self.assertGreaterEqual(window.curve.temp_plot.width(), 750)
                for action in window.curve_visibility_actions.values():
                    self.assertFalse(action.icon().isNull())
                for widget in (window.ror_window_combo, window.apply_time_range_button):
                    self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width())
                window.resize(1920, 1080)
                self.app.processEvents()
                self.assertTrue(window.forecast_yellow_chip.isVisible())
                self.assertTrue(window.forecast_fc_chip.isVisible())
            finally:
                window.close()

    def test_dense_manual_ticks_keep_spacing_without_overlapping_labels(self):
        chart = CurveWidget()
        self.addCleanup(chart.close)
        chart.resize(628, 416)
        chart.set_axis_range("x", 0.0, 900.0, 30.0, auto=False)
        chart.show()
        self.app.processEvents()
        axis = chart.plot_item.getAxis("bottom")
        values = list(range(0, 901, 30))
        for font_size in (10, 12, 22):
            chart.apply_view_settings(font_size, 1.0)
            self.app.processEvents()
            labels = axis.tickStrings(values, 1.0, 30.0)
            metrics = QtGui.QFontMetricsF(axis.style["tickFont"])
            end = float("-inf")
            count = 0
            for value, label in zip(values, labels):
                if not label:
                    continue
                center = value * axis.width() / 900.0
                half = metrics.horizontalAdvance(label) / 2.0
                self.assertGreaterEqual(center - half, end + 11.9)
                end = center + half
                count += 1
            self.assertGreater(count, 2)
            self.assertLess(count, len(values))
            self.assertEqual(chart.axis_settings()["x"]["major"], 30.0)
            self.assertEqual(chart.time_range(), (0.0, 900.0))
