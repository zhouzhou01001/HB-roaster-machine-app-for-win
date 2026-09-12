import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets
from app.roast.workflow import RoastFlowState
from app.roast.ror import calculate_ror
from app.ui.main_window import MainWindow


class Device(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.connections = 0

    def connect(self, *args, **kwargs):
        self.connections += 1

    def disconnect(self):
        pass


class ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.device = Device()
        factory = patch("app.ui.main_window.SerialManager", return_value=self.device)
        factory.start()
        self.addCleanup(factory.stop)
        self.clock = patch("app.ui.main_window.time.monotonic", return_value=1000.0).start()
        self.addCleanup(patch.stopall)
        self.window = MainWindow(self.root / "test.hbroast", self.root / "channel.json")
        self.addCleanup(self.window.close)
        self.window._start_debug_logger = lambda: None
        self.window.selected_source = "COM_TEST"
        self.window._begin_monitoring()

    def sample(self, seconds, bt=100.0):
        self.window._on_sample_raw({"time_s": seconds, "CH1": 110.0,
                                   "CH2": 120.0, "CH3": bt, "CH4": 115.0})

    def charge(self):
        self.sample(100.0)
        self.window._begin_monitoring()
        self.sample(100.0)
        self.window._on_event_requested("CHARGE", 0.0, "")

    def test_collection_does_not_populate_formal_or_preview_curve(self):
        self.sample(100.0)
        self.assertEqual(self.window.samples, [])
        self.assertTrue(self.window._has_valid_device_sample)
        self.assertEqual(self.window.metric_time.value_widget.text(), "00:00")

    def test_successful_reconnect_preserves_elapsed_time_and_marks_gap(self):
        self.charge()
        self.clock.return_value = 1010.0
        self.sample(110.0)
        self.window._on_state_changed(False)
        self.clock.return_value = 1020.0
        self.window._attempt_reconnect()
        self.window._on_state_changed(True)
        self.sample(0.0, 150.0)
        self.assertEqual(len(self.window.samples), 2)
        self.assertAlmostEqual(self.window.samples[-1]["time_s"], 20.0)
        self.assertTrue(self.window.samples[-1]["gap_before"])
        self.assertIsNone(self.window.samples[-1]["bt_ror"])
        self.assertEqual(self.window.events[0]["time_s"], 0.0)

    def test_time_rollback_without_reconnect_is_rejected(self):
        self.charge()
        self.sample(99.0)
        self.assertEqual(self.window.samples, [])

    def test_retry_exhaustion_keeps_batch_and_allows_manual_resume(self):
        self.charge()
        self.clock.return_value = 1010.0
        self.sample(110.0)
        self.window._reconnect_attempts = self.window._max_reconnect_attempts
        self.window._schedule_reconnect()
        self.assertEqual(self.window.workflow.state, RoastFlowState.ROASTING)
        self.assertFalse(self.window.session_active)
        self.assertTrue(self.window.start_monitor_button.isEnabled())
        self.clock.return_value = 1020.0
        self.window._begin_monitoring()
        self.sample(0.0)
        self.assertEqual(len(self.window.samples), 2)
        self.assertEqual(len(self.window.events), 1)
        self.assertEqual(self.window.workflow.state, RoastFlowState.ROASTING)

    def test_empty_archive_does_not_replace_current_database(self):
        self.window._set_session_active(False)
        previous_db = self.window.db
        self.window._replace_session_buffer([{"time_s": 1.0, "bt": 100.0}])
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(self.root / "empty.hbroast"), "")), patch("PySide6.QtWidgets.QMessageBox.information"):
            self.window._open_batch()
        self.assertIs(self.window.db, previous_db)
        self.assertEqual(self.window.samples[0]["bt"], 100.0)

    def test_finished_live_samples_do_not_restart_roast_timer(self):
        self.charge()
        self.clock.return_value = 1010.0
        self.sample(110.0)
        self.window._on_event_requested("DROP", 10.0, "")
        self.clock.return_value = 1020.0
        self.sample(120.0)
        self.assertEqual(self.window.metric_time.value_widget.text(), "00:10")
        self.assertEqual(len(self.window.samples), 1)

    def test_charge_clock_advances_without_new_samples(self):
        self.charge()
        self.clock.return_value = 1015.0
        self.assertEqual(self.window._get_time_s(), 15.0)

    def test_disconnected_preparation_cannot_accept_charge(self):
        self.sample(100.0)
        self.window._begin_monitoring()
        self.window._on_state_changed(False)
        self.window._on_event_requested("CHARGE", 0.0, "")
        self.assertEqual(self.window.workflow.state, RoastFlowState.READY_TO_CHARGE)
        self.assertEqual(self.window.events, [])

    def test_ror_recalculation_does_not_bridge_disconnect(self):
        samples = [{"time_s": float(i), "bt": 100.0} for i in range(8)]
        samples.append({"time_s": 8.0, "bt": 180.0, "gap_before": True})
        self.assertIsNone(calculate_ror(samples, window=10)["bt_ror"])

    def test_open_new_archive_loads_four_channels_and_metadata(self):
        from app.database.database import RoastDatabase
        path = self.root / "archived.hbroast"
        db = RoastDatabase(path)
        try:
            db.archive_session([{"time_s": 1.0, "bt": 123.0, "work": 140.0, "work_ror": 12.0}],
                               [{"event_type": "DROP", "time_s": 1.0}], [], {"coffee_name": "archived"})
        finally:
            db.close()
        self.window._set_session_active(False)
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(path), "")), patch("PySide6.QtWidgets.QMessageBox.information"):
            self.window._open_batch()
        self.assertEqual(self.window.samples[0]["work"], 140.0)
        self.assertEqual(self.window.samples[0]["work_ror"], 12.0)
        self.assertEqual(self.window._batch_metadata["coffee_name"], "archived")

    def test_import_extensions_reach_live_readout_and_export_buffer(self):
        self.window._replace_session_buffer([{"time_s": 1.0, "bt": 123.0,
            "raw_payload": {"raw_import_sample": {"work": 140.0, "work_ror": 12.0, "gap_before": True}}}])
        self.window._refresh_current_data(self.window.samples[-1])
        self.assertEqual(self.window.metric_exhaust.value_widget.text(), "140.0")
        self.assertEqual(self.window.metric_exhaust.sub_value_widget.text(), "12")
        self.assertTrue(self.window.samples[0]["gap_before"])
