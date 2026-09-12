import inspect
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

from app.database.database import RoastDatabase
from app.ui.action_panel import ActionPanel
from app.ui.curve_widget import CurveWidget
from app.ui.main_window import MainWindow


SAMPLES = [
    {"time_s": 0.0, "it": 120.0, "et": 128.0, "bt": 92.0, "it_ror": 1.0, "et_ror": 1.2, "bt_ror": 0.8},
    {"time_s": 60.0, "it": 132.0, "et": 142.0, "bt": 104.0, "it_ror": 2.0, "et_ror": 2.4, "bt_ror": 1.8},
    {"time_s": 120.0, "it": 145.0, "et": 158.0, "bt": 116.0, "it_ror": 2.5, "et_ror": 2.8, "bt_ror": 2.1},
    {"time_s": 180.0, "it": 157.0, "et": 172.0, "bt": 130.0, "it_ror": 2.2, "et_ror": 2.3, "bt_ror": 2.0},
]

EVENTS = [
    {"event_type": "CHARGE", "time_s": 0.0, "bt": 92.0},
    {"event_type": "YELLOW", "time_s": 120.0, "bt": 116.0},
]

BACKGROUND = [
    {"time_s": 0.0, "bt": 90.0, "bt_ror": 0.6},
    {"time_s": 120.0, "bt": 113.0, "bt_ror": 1.9},
]


class ReleaseIncrementBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.workspace.cleanup)

    def _chart(self) -> CurveWidget:
        chart = CurveWidget()
        chart.resize(900, 420)
        chart.show()
        self.app.processEvents()
        self.addCleanup(self._cleanup_widget, chart)
        return chart

    def _window(self) -> MainWindow:
        window = MainWindow(
            db_path=Path(self.workspace.name) / "release_increment.hbroast",
            channel_mapping_path=Path(self.workspace.name) / "channel.json",
        )
        window.resize(1280, 720)
        window.show()
        self.app.processEvents()
        self.addCleanup(self._cleanup_widget, window)
        return window

    def _cleanup_widget(self, widget) -> None:
        try:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()
        except RuntimeError:
            pass

    def _apply_axis_range(self, chart: CurveWidget, axis: str, minimum, maximum, major_tick):
        if hasattr(chart, "set_axis_range"):
            return chart.set_axis_range(axis, minimum, maximum, major_tick)
        method_names = {
            "x": ("set_x_axis_range", "set_time_axis_range"),
            "left_y": ("set_left_y_axis_range", "set_temperature_axis_range"),
            "right_y": ("set_right_y_axis_range", "set_ror_axis_range"),
        }[axis]
        for name in method_names:
            method = getattr(chart, name, None)
            if callable(method):
                return method(minimum, maximum, major_tick)
        self.fail(f"missing {axis} min/max/major tick API")

    def _axis_state(self, chart: CurveWidget, axis: str):
        if hasattr(chart, "axis_range"):
            return chart.axis_range(axis)
        if axis == "x":
            left, right = chart.time_range()
            return left, right, getattr(chart, "x_major_tick", None)
        if axis == "left_y":
            bottom, top = chart.temp_plot.getViewBox().viewRange()[1]
            return bottom, top, getattr(chart, "left_y_major_tick", None)
        bottom, top = chart.ror_view.viewRange()[1]
        return bottom, top, getattr(chart, "right_y_major_tick", None)

    def test_x_left_y_and_right_y_axis_range_and_tick_validation(self) -> None:
        chart = self._chart()
        chart.set_data(SAMPLES, {"BT_RoR": [(180.0, 2.0), (240.0, 1.8)]}, EVENTS)
        self._apply_axis_range(chart, "x", 10.0, 170.0, 20.0)
        self._apply_axis_range(chart, "left_y", 80.0, 220.0, 10.0)
        self._apply_axis_range(chart, "right_y", -5.0, 10.0, 1.0)
        self.app.processEvents()

        for axis, expected in {
            "x": (10.0, 170.0, 20.0),
            "left_y": (80.0, 220.0, 10.0),
            "right_y": (0.0, 10.0, 1.0),
        }.items():
            state = self._axis_state(chart, axis)
            self.assertAlmostEqual(state[0], expected[0], places=2)
            self.assertAlmostEqual(state[1], expected[1], places=2)
            self.assertEqual(state[2], expected[2], f"{axis} major tick was not retained")

        for axis, invalid in (
            ("x", (170.0, 10.0, 20.0)),
            ("x", ("", 100.0, 10.0)),
            ("left_y", (80.0, 220.0, 0.0)),
            ("right_y", (-99999.0, 99999.0, 1.0)),
        ):
            before = self._axis_state(chart, axis)
            with self.assertRaises((TypeError, ValueError)):
                self._apply_axis_range(chart, axis, *invalid)
            self.assertEqual(self._axis_state(chart, axis), before)

    def test_realtime_data_extends_x_and_keeps_default_temperature_range(self) -> None:
        chart = self._chart()
        chart.set_auto_follow(True)
        chart.set_data(SAMPLES[:2])
        self.app.processEvents()
        first_x = chart.time_range()
        first_left_y = chart.temp_plot.getViewBox().viewRange()[1]
        self.assertAlmostEqual(first_left_y[0], 0.0, places=2)
        self.assertAlmostEqual(first_left_y[1], 400.0, places=2)

        chart.set_data(SAMPLES + [{"time_s": 900.0, "it": 230.0, "et": 240.0, "bt": 205.0, "bt_ror": 12.0}])
        self.app.processEvents()
        followed_x = chart.time_range()
        followed_left_y = chart.temp_plot.getViewBox().viewRange()[1]
        self.assertEqual(first_x[0], 0.0)
        self.assertEqual(followed_x[0], 0.0)
        self.assertGreaterEqual(followed_x[1], 900.0)
        self.assertAlmostEqual(followed_left_y[0], 0.0, places=2)
        self.assertAlmostEqual(followed_left_y[1], 400.0, places=2)

        chart.set_time_range(30.0, 150.0)
        manual_x = chart.time_range()
        chart.set_data(SAMPLES + [{"time_s": 1200.0, "it": 240.0, "et": 250.0, "bt": 215.0, "bt_ror": 10.0}])
        self.app.processEvents()
        self.assertEqual(chart.time_range(), manual_x)

        chart.set_auto_follow(True)
        chart.set_data(SAMPLES + [{"time_s": 1500.0, "it": 250.0, "et": 260.0, "bt": 225.0, "bt_ror": 9.0}])
        self.app.processEvents()
        self.assertGreaterEqual(chart.time_range()[1], 1500.0)

    def test_damper_and_rpm_zero_to_ten_steps_persist_load_and_normalize_old_data(self) -> None:
        panel = ActionPanel()
        self.addCleanup(panel.close)
        self.assertEqual(panel.damper_spin.minimum(), 0.0)
        self.assertEqual(panel.damper_spin.maximum(), 10.0)
        self.assertEqual(panel.rpm_spin.minimum(), 0.0)
        self.assertEqual(panel.rpm_spin.maximum(), 10.0)

        db = RoastDatabase(Path(self.workspace.name) / "actions.hbroast")
        self.addCleanup(db.close)
        batch_id = db.create_batch(coffee_name="actions")
        db.insert_manual_action(batch_id, 10.0, 0.0, 0.0, 0.0, "zero")
        db.insert_manual_action(batch_id, 20.0, 10.0, 10.0, 10.0, "ten")
        db.insert_manual_action(batch_id, 30.0, 5.0, 50.0, 150.0, "legacy")
        loaded = db.load_manual_actions(batch_id)
        self.assertEqual(len(loaded), 3)

        chart = self._chart()
        chart.set_actions(loaded)
        damper_y = list(chart.damper_curve.getData()[1])
        rpm_y = list(chart.rpm_curve.getData()[1])
        self.assertEqual(damper_y, [0.0, 100.0, 50.0])
        self.assertEqual(rpm_y, [0.0, 100.0, 50.0])

    def _pdf_control(self, window: MainWindow):
        for name in ("act_export_pdf", "export_pdf_action", "pdf_export_action"):
            control = getattr(window, name, None)
            if control is not None:
                return control
        for name in ("export_pdf_button", "pdf_export_button"):
            control = getattr(window, name, None)
            if control is not None:
                return control
        for action in window.findChildren(QtGui.QAction):
            if "PDF" in action.text().upper():
                return action
        for button in window.findChildren(QtWidgets.QAbstractButton):
            if "PDF" in button.text().upper():
                return button
        self.fail("missing PDF export action/button")

    def test_pdf_export_button_enablement_requires_finished_roast_or_backup_curve(self) -> None:
        window = self._window()
        control = self._pdf_control(window)
        window._set_session_active(True)
        window.curve.set_background_profile([])
        window.background_profile_name = ""
        self.app.processEvents()
        self.assertFalse(control.isEnabled(), "PDF export must be disabled while roasting without a backup/background curve")

        window.curve.set_background_profile(BACKGROUND, "backup")
        window.background_profile_name = "backup"
        if hasattr(window, "_refresh_pdf_export_state"):
            window._refresh_pdf_export_state()
        self.app.processEvents()
        self.assertTrue(control.isEnabled(), "PDF export must be enabled after a backup/background curve is loaded")

        window.curve.set_background_profile([])
        window.background_profile_name = ""
        window._set_session_active(False)
        if hasattr(window, "_refresh_pdf_export_state"):
            window._refresh_pdf_export_state()
        self.app.processEvents()
        self.assertFalse(
            control.isEnabled(),
            "forcing acquisition inactive does not mean the current roast was confirmed as finished",
        )

        window._set_session_active(True)
        window.selected_source = "SIMULATION"
        window._on_sample_raw({"time_s": 0.0, "CH1": 120.0, "CH2": 130.0, "CH3": 100.0, "CH4": 110.0})
        window.workflow.start_roast()
        window._on_event_requested("CHARGE", 0.0, "")
        window._on_sample_raw({"time_s": 1.0, "CH1": 120.0, "CH2": 130.0, "CH3": 100.0, "CH4": 110.0})
        window._on_event_requested("DROP", 1.0, "")
        window._request_finish_batch()
        self.app.processEvents()
        self.assertIsNotNone(window._preview_dialog)
        window._preview_dialog.confirm_button.click()
        self.app.processEvents()
        self.assertTrue(
            control.isEnabled(),
            "PDF export must be enabled after the end-roast preview is confirmed",
        )

    def _export_pdf(self, window: MainWindow, target: Path) -> None:
        for name in ("export_pdf", "_export_pdf", "_export_pdf_report", "_export_current_pdf"):
            method = getattr(window, name, None)
            if callable(method):
                parameters = inspect.signature(method).parameters
                if len(parameters) >= 1:
                    method(target)
                else:
                    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(target), "")):
                        method()
                return
        self.fail("missing callable PDF export method")

    def test_pdf_export_renders_visible_chart_layers_and_excludes_hidden_curves(self) -> None:
        window = self._window()
        window._batch_id = window.db.create_batch(coffee_name="pdf")
        window.samples = list(SAMPLES)
        window.actions = [
            {"time_s": 30.0, "gas_kpa": 2.0, "damper_pct": 2.0, "rpm": 2.0, "note": "low"},
            {"time_s": 150.0, "gas_kpa": 8.0, "damper_pct": 8.0, "rpm": 8.0, "note": "high"},
        ]
        window.events = list(EVENTS)
        window.curve.set_data(window.samples, {"BT_RoR": [(180.0, 2.0), (240.0, 1.8)]}, window.events)
        window.curve.set_actions(window.actions)
        window.curve.set_targets(150.0, 195.0)
        window.curve.set_background_profile(BACKGROUND, "backup")
        window.curve.set_time_range(0.0, 240.0)
        window.curve.set_curve_visibility("BT", False)
        self.app.processEvents()

        pdf_dir = Path("tmp/pdfs")
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / "release_increment_export.pdf"
        png_prefix = pdf_dir / "release_increment_export"
        self._export_pdf(window, pdf_path)
        self.assertTrue(pdf_path.exists(), "PDF export did not create a file")
        self.assertGreater(pdf_path.stat().st_size, 1000)

        if shutil.which("pdfinfo") is None or shutil.which("pdftoppm") is None:
            return

        try:
            info_result = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                text=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError:
            return
        self.assertEqual(info_result.returncode, 0, info_result.stderr)
        self.assertIn("Pages:", info_result.stdout)

        try:
            render_result = subprocess.run(
                ["pdftoppm", "-png", str(pdf_path), str(png_prefix)],
                text=True,
                capture_output=True,
                timeout=30,
            )
        except FileNotFoundError:
            return
        self.assertEqual(render_result.returncode, 0, render_result.stderr)
        pngs = sorted(pdf_dir.glob("release_increment_export-*.png"))
        self.assertTrue(pngs, "pdftoppm did not render any PNG pages")
        image = QtGui.QImage(str(pngs[0]))
        self.assertFalse(image.isNull(), f"rendered PNG cannot be opened: {pngs[0]}")
        self.assertGreaterEqual(image.width(), 600)
        self.assertGreaterEqual(image.height(), 400)
        import numpy as np
        image = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
        rgb = pixels[:, :image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, :3]
        plot = rgb[image.height() // 10:image.height() * 85 // 100, image.width() // 10:image.width() * 90 // 100]
        chromatic = plot.max(axis=2).astype(int) - plot.min(axis=2).astype(int) > 40
        self.assertGreater(int(chromatic.sum()), 100, "rendered PDF plot has no colored curves")
        self.assertGreater(len(np.unique(plot[chromatic] // 32, axis=0)), 3)
        from app.export.chart_pdf import _pdf_font_family
        font = QtGui.QRawFont.fromFont(QtGui.QFont(_pdf_font_family()))
        self.assertTrue(font.supportsCharacter("0"))
        self.assertTrue(font.supportsCharacter("温"))


if __name__ == "__main__":
    unittest.main()
