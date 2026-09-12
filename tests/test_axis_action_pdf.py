import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.database.database import RoastDatabase
from app.export.chart_pdf import export_chart_pdf
from app.export.json_export import export_roast_json
from app.ui.action_panel import ActionPanel
from app.ui.axis_settings_dialog import AxisSettingsDialog
from app.ui.curve_widget import CurveWidget
from app.ui.main_window import MainWindow


class AxisActionPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_three_axis_range_and_span_validation_preserve_old_view(self):
        chart = CurveWidget()
        chart.set_data([
            {"time_s": 0.0, "it": 120.0, "et": 130.0, "bt": 100.0, "bt_ror": 4.0},
            {"time_s": 60.0, "it": 180.0, "et": 190.0, "bt": 160.0, "bt_ror": 8.0},
        ])
        chart.set_axis_range("x", 10.0, 50.0, 10.0, auto=False)
        chart.set_axis_range("left", 100.0, 220.0, 20.0, auto=False)
        chart.set_axis_range("right", -5.0, 15.0, 5.0, auto=False)
        old = chart.export_snapshot()
        self.assertEqual(old["x_range"], (10.0, 50.0))
        self.assertEqual(old["left_range"], (100.0, 220.0))
        self.assertEqual(old["right_range"], (0.0, 15.0))
        with self.assertRaises(ValueError):
            chart.set_axis_range("left", 220.0, 100.0, 10.0, auto=False)
        with self.assertRaises(ValueError):
            chart.set_axis_range("x", -1.0, 30.0, 5.0, auto=False)
        self.assertEqual(chart.export_snapshot()["left_range"], (100.0, 220.0))

        dialog = AxisSettingsDialog(chart.axis_settings(), chart.axis_auto_states())
        dialog.controls["left"]["min"].setValue(100.0)
        dialog.controls["left"]["span"].setValue(80.0)
        self.assertAlmostEqual(dialog.controls["left"]["max"].value(), 180.0)
        dialog.close()

    def test_y_auto_scaling_is_independent_and_respects_manual_range(self):
        chart = CurveWidget()
        chart.set_data([{"time_s": 0.0, "it": 100.0, "et": 110.0, "bt": 90.0, "bt_ror": 2.0}])
        chart.set_axis_range("left", 80.0, 140.0, 10.0, auto=False)
        chart.set_data([{"time_s": 0.0, "it": 100.0, "et": 110.0, "bt": 90.0}, {"time_s": 60.0, "it": 300.0, "et": 310.0, "bt": 280.0}])
        self.assertEqual(chart.export_snapshot()["left_range"], (80.0, 140.0))
        self.assertFalse(chart.axis_auto_states()["left"])
        chart.set_axis_auto("left", True)
        self.assertTrue(chart.axis_auto_states()["left"])
        self.assertGreater(chart.export_snapshot()["left_range"][1], 300.0)

    def test_main_window_persists_three_axis_ranges_ticks_and_auto_across_rebuild(self):
        original_settings = self._settings_snapshot()
        self.addCleanup(self._restore_settings, original_settings)
        self._restore_settings({})
        manual_settings = {
            "x": {"min": 12.0, "max": 312.0, "major": 30.0, "auto": False},
            "left": {"min": 75.0, "max": 245.0, "major": 17.0, "auto": False},
            "right": {"min": 0.0, "max": 16.0, "major": 2.0, "auto": False},
        }
        auto_settings = {
            "x": {"min": 20.0, "max": 620.0, "major": 60.0, "auto": True},
            "left": {"min": 60.0, "max": 260.0, "major": 20.0, "auto": True},
            "right": {"min": 0.0, "max": 18.0, "major": 2.0, "auto": True},
        }
        invalid_settings = {
            "x": {"min": 200.0, "max": 100.0, "major": 30.0, "auto": False},
            "left": {"min": 75.0, "max": 245.0, "major": 0.0, "auto": True},
            "right": {"min": -99999.0, "max": 99999.0, "major": 2.0, "auto": False},
        }

        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        directory = temporary.name

        first = self._build_temp_window(directory, "first")
        self.addCleanup(self._close_window, first)
        self._accept_axis_dialog(first, manual_settings)
        self._assert_axis_state(first, manual_settings)
        self._close_window(first)

        rebuilt = self._build_temp_window(directory, "rebuilt")
        self.addCleanup(self._close_window, rebuilt)
        self._assert_axis_state(rebuilt, manual_settings)
        before_ranges = {
            axis: rebuilt.curve.axis_range(axis)
            for axis in ("x", "left", "right")
        }
        before_auto = rebuilt.curve.axis_auto_states()

        self._accept_axis_dialog(rebuilt, invalid_settings)
        self.assertEqual(
            {axis: rebuilt.curve.axis_range(axis) for axis in ("x", "left", "right")},
            before_ranges,
        )
        self.assertEqual(rebuilt.curve.axis_auto_states(), before_auto)
        self._accept_axis_dialog(rebuilt, auto_settings)
        self.assertEqual(rebuilt.curve.axis_auto_states(), {"x": True, "left": True, "right": True})
        self._close_window(rebuilt)

        after_auto = self._build_temp_window(directory, "after_auto")
        self.addCleanup(self._close_window, after_auto)
        self.assertEqual(after_auto.curve.axis_auto_states(), {"x": True, "left": True, "right": True})
        for axis, values in auto_settings.items():
            minimum, maximum, major = after_auto.curve.axis_range(axis)
            if axis == "x":
                interval_count = (maximum - minimum) / major
                self.assertGreaterEqual(interval_count, 3.0)
                self.assertLessEqual(interval_count, 12.0)
            else:
                self.assertAlmostEqual(major, values["major"], places=2)
        self._close_window(after_auto)

    def _build_temp_window(self, directory: str, stem: str) -> MainWindow:
        window = MainWindow(
            db_path=Path(directory) / f"{stem}.hbroast",
            channel_mapping_path=Path(directory) / f"{stem}_channel.json",
        )
        self.app.processEvents()
        return window

    def _close_window(self, window: MainWindow) -> None:
        try:
            window.close()
            window.db.close()
            self.app.processEvents()
        except RuntimeError:
            pass

    def _accept_axis_dialog(self, window: MainWindow, values: dict) -> None:
        class AcceptedAxisDialog:
            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                return QtWidgets.QDialog.DialogCode.Accepted

            def values(self):
                return values

        with patch("app.ui.main_window.AxisSettingsDialog", AcceptedAxisDialog):
            window._open_axis_settings()
        QtCore.QSettings("HB", "RoastMonitor").sync()
        self.app.processEvents()

    def _assert_axis_state(self, window: MainWindow, expected: dict) -> None:
        aliases = {"x": "x", "left": "left", "right": "right"}
        for axis, chart_axis in aliases.items():
            minimum, maximum, major = window.curve.axis_range(chart_axis)
            self.assertAlmostEqual(minimum, expected[axis]["min"], places=2)
            self.assertAlmostEqual(maximum, expected[axis]["max"], places=2)
            self.assertAlmostEqual(major, expected[axis]["major"], places=2)
        self.assertEqual(
            window.curve.axis_auto_states(),
            {axis: bool(values["auto"]) for axis, values in expected.items()},
        )

    def _settings_snapshot(self) -> dict:
        settings = QtCore.QSettings("HB", "RoastMonitor")
        return {key: settings.value(key) for key in settings.allKeys()}

    def _restore_settings(self, snapshot: dict) -> None:
        settings = QtCore.QSettings("HB", "RoastMonitor")
        settings.clear()
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()

    def test_action_panel_database_and_legacy_values_use_zero_to_ten_levels(self):
        panel = ActionPanel()
        self.assertEqual(panel.damper_spin.maximum(), 10.0)
        self.assertEqual(panel.rpm_spin.maximum(), 10.0)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            db = RoastDatabase(Path(directory) / "actions.hbroast")
            self.addCleanup(db.close)
            roast_id = db.create_batch()
            db.insert_manual_action(roast_id, 1.0, 2.0, 4.0, 7.0, "new")
            db.conn.execute(
                "INSERT INTO manual_actions (roast_id, time_s, gas_kpa, damper_pct, rpm, note) VALUES (?, ?, ?, ?, ?, ?)",
                (roast_id, 2.0, 3.0, 25.0, 150.0, "legacy"),
            )
            db.conn.commit()
            actions = db.load_manual_actions(roast_id)
            self.assertEqual(actions[0]["damper_level"], 4.0)
            self.assertEqual(actions[0]["rpm_level"], 7.0)
            self.assertEqual(actions[1]["damper_level"], 2.5)
            self.assertEqual(actions[1]["rpm_level"], 5.0)
            raw = db.conn.execute("SELECT damper_level, rpm_level, damper_pct, rpm FROM manual_actions WHERE time_s = 1.0").fetchone()
            self.assertEqual(tuple(raw), (4.0, 7.0, None, None))
            db.close()

    def test_pdf_button_condition_snapshot_and_vector_export(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(
                db_path=Path(directory) / "window.hbroast",
                channel_mapping_path=Path(directory) / "channel.json",
            )
            self.addCleanup(window.db.close)
            self.addCleanup(window.close)
            self.assertFalse(window.export_pdf_button.isEnabled())
            window.background_profile_name = "参考曲线"
            window._update_chart_pdf_availability()
            self.assertTrue(window.export_pdf_button.isEnabled())
            window.background_profile_name = ""
            window._roast_finished = False
            window._update_chart_pdf_availability()
            self.assertFalse(window.act_export_pdf.isEnabled())
            window._roast_finished = True
            window._update_chart_pdf_availability()
            self.assertTrue(window.act_export_pdf.isEnabled())

            chart = window.curve
            chart.set_data([
                {"time_s": 0.0, "it": 120.0, "et": 130.0, "bt": 100.0, "bt_ror": 4.0},
                {"time_s": 60.0, "it": 180.0, "et": 190.0, "bt": 160.0, "bt_ror": 8.0},
            ], events=[{"event_type": "YELLOW", "time_s": 30.0}])
            chart.set_targets(150.0, 195.0)
            chart.set_background_profile([{"time_s": 0.0, "bt": 95.0, "bt_ror": 3.0}, {"time_s": 60.0, "bt": 155.0, "bt_ror": 7.0}], "参考")
            chart.set_curve_visibility("ET", False)
            snapshot = chart.export_snapshot()
            self.assertFalse(next(item for item in snapshot["curves"] if item["key"] == "ET")["visible"])
            self.assertEqual(len(snapshot["events"]), 1)
            self.assertEqual(len(snapshot["targets"]), 2)
            target = Path(directory) / "chart.pdf"
            export_chart_pdf(target, chart)
            self.assertGreater(target.stat().st_size, 1000)

            json_target = Path(directory) / "actions.json"
            export_roast_json({}, [], [], [{"time_s": 1.0, "gas_kpa": 2.0, "damper_pct": 40.0, "rpm": 150.0}], json_target)
            payload = json_target.read_text(encoding="utf-8")
            self.assertIn('"damper_level": 4.0', payload)
            self.assertIn('"rpm_level": 5.0', payload)
            window.close()

    def test_axis_settings_persist_across_main_window_recreation_without_invalid_pollution(self):
        qsettings = QtCore.QSettings("HB", "RoastMonitor")
        old_values = {
            key: qsettings.value(key)
            for key in qsettings.allKeys()
            if key.startswith("axis/")
        }
        qsettings.remove("axis")
        qsettings.sync()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            first = MainWindow(
                db_path=Path(directory) / "first.hbroast",
                channel_mapping_path=Path(directory) / "channel.json",
            )
            desired = {axis: dict(first.curve.axis_settings()[axis]) for axis in ("x", "left", "right")}
            desired["x"].update(min=10.0, max=170.0, major=20.0, auto=False)
            desired["left"].update(min=80.0, max=220.0, major=10.0, auto=False)
            desired["right"].update(min=-5.0, max=10.0, major=1.0, auto=True)
            first.curve.apply_axis_settings(desired)
            first._save_axis_settings()
            first.close()

            second = MainWindow(
                db_path=Path(directory) / "second.hbroast",
                channel_mapping_path=Path(directory) / "channel.json",
            )
            self.addCleanup(second.close)
            self.assertEqual(second.curve.axis_range("x"), (10.0, 170.0, 20.0))
            self.assertEqual(second.curve.axis_range("left_y"), (80.0, 220.0, 10.0))
            self.assertEqual(second.curve.axis_range("right_y"), (0.0, 10.0, 1.0))
            self.assertEqual(second.curve.axis_auto_states(), {"x": False, "left": False, "right": True})
            dialog = AxisSettingsDialog(second.curve.axis_settings(), second.curve.axis_auto_states())
            self.assertAlmostEqual(dialog.controls["left"]["span"].value(), 140.0)
            dialog.close()

            persisted_before_invalid = {
                key: qsettings.value(key)
                for key in qsettings.allKeys()
                if key.startswith("axis/")
            }
            bad = {axis: dict(second.curve.axis_settings()[axis]) for axis in ("x", "left", "right")}
            bad["x"].update(min=200.0, max=100.0, major=10.0, auto=False)
            with self.assertRaises(ValueError):
                second.curve.apply_axis_settings(bad)
            self.assertEqual(second.curve.axis_range("x"), (10.0, 170.0, 20.0))
            persisted_after_invalid = {
                key: qsettings.value(key)
                for key in qsettings.allKeys()
                if key.startswith("axis/")
            }
            self.assertEqual(persisted_after_invalid, persisted_before_invalid)
            second.close()
        qsettings.remove("axis")
        for key, value in old_values.items():
            qsettings.setValue(key, value)
        qsettings.sync()


if __name__ == "__main__":
    unittest.main()
