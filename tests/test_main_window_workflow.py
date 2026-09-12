from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.database.database import SessionArchiveResult
from app.roast.workflow import RoastFlowState, RoastStage
from app.ui.main_window import MainWindow


class FakeSerialManager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.connect_calls: list[tuple[str, int, str | None]] = []
        self.disconnect_calls = 0

    def connect(self, port: str, baudrate: int = 9600, profile: str | None = None) -> None:
        self.connect_calls.append((port, baudrate, profile))

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class MainWindowWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        root = Path(self.workspace.name)
        self.manager = FakeSerialManager()
        self.serial_patch = patch("app.ui.main_window.SerialManager", return_value=self.manager)
        self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        self.window = MainWindow(
            db_path=root / "workflow.hbroast",
            channel_mapping_path=root / "channels.json",
        )
        self.window._start_debug_logger = lambda: None
        self.window._stop_debug_logger = lambda *_args, **_kwargs: None
        self.window.curve.set_data = lambda *_args, **_kwargs: None
        self.window.curve.set_events = lambda *_args, **_kwargs: None
        self.window.curve.set_actions = lambda *_args, **_kwargs: None
        self.window.curve.set_targets = lambda *_args, **_kwargs: None
        self.window.db.insert_sample = Mock(side_effect=AssertionError("incremental sample write"))
        self.window.db.insert_event = Mock(side_effect=AssertionError("incremental event write"))
        self.window.db.insert_manual_action = Mock(side_effect=AssertionError("incremental action write"))
        self.window.db.archive_session = Mock(
            return_value=SessionArchiveResult(charge_id=11, batch_id=12, charge_number=1)
        )
        self.addCleanup(self._close_window)

    def _close_window(self) -> None:
        if self.window is None:
            return
        self.window.close()
        self.window = None
        QtWidgets.QApplication.processEvents()

    def _start(self) -> None:
        self.window.selected_source = "SIMULATION"
        self.window.selected_profile = "SIMULATION"
        self.window.selected_baudrate = 0
        self.window._begin_monitoring()
        self._sample(0.0)
        self.window._begin_monitoring()
        self.assertEqual(self.window.workflow.state, RoastFlowState.READY_TO_CHARGE)

    def _sample(self, time_s: float, bt: float = 100.0) -> None:
        self.manager.sample_received.emit(
            {
                "time_s": time_s,
                "CH1": bt + 20.0,
                "CH2": bt + 10.0,
                "CH3": bt,
                "source": "SIMULATION",
            }
        )
        QtWidgets.QApplication.processEvents()

    def _drop(self, time_s: float = 10.0) -> None:
        self.window._on_event_requested("DROP", time_s, "")

    def _confirm_archive(self) -> None:
        self.window._request_finish_batch()
        QtWidgets.QApplication.processEvents()
        self.assertIsNotNone(self.window._preview_dialog)
        self.window._preview_dialog.confirm_button.click()
        QtWidgets.QApplication.processEvents()

    def test_samples_events_and_actions_remain_in_memory_until_confirmation(self) -> None:
        self._start()
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertTrue(self.window.session_active)
        self.assertEqual(len(self.manager.connect_calls), 1)

        self._sample(1.0)
        self.window._on_event_requested("CHARGE", 1.0, "入豆")
        self._sample(2.0)
        self.window._on_action_added(2.5, 6.0, 8.0, "加火")

        self.assertEqual(len(self.window.samples), 1)
        self.assertEqual([item["event_type"] for item in self.window.events], ["CHARGE"])
        self.assertEqual(self.window.events[0]["time_s"], 0.0)
        self.assertEqual(self.window.actions[-1]["gas_mbar"], 25.0)
        self.window.db.insert_sample.assert_not_called()
        self.window.db.insert_event.assert_not_called()
        self.window.db.insert_manual_action.assert_not_called()
        self.window.db.archive_session.assert_not_called()

    def test_drop_locks_roast_but_keeps_sampling_and_confirmation_archives(self) -> None:
        self._start()
        self.window._on_event_requested("CHARGE", 0.0, "入豆")
        self._sample(1.0)
        self._drop(2.0)
        self.assertEqual(self.window.workflow.stage, RoastStage.AWAIT_END)
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.manager.disconnect_calls, 0)

        self.window._end_monitoring()
        self.assertEqual(self.window.workflow.stage, RoastStage.AWAIT_END)
        self.assertEqual(self.manager.disconnect_calls, 0)
        self._sample(2.0, 101.0)
        self.assertEqual(len(self.window.samples), 1)

        self.window._request_finish_batch()
        QtWidgets.QApplication.processEvents()
        self.window.db.archive_session.assert_not_called()
        self.window._preview_dialog.confirm_button.click()
        QtWidgets.QApplication.processEvents()

        self.window.db.archive_session.assert_called_once()
        archived_samples, archived_events, archived_actions, metadata = (
            self.window.db.archive_session.call_args.args
        )
        self.assertEqual(len(archived_samples), 1)
        self.assertEqual(archived_events[-1]["event_type"], "DROP")
        self.assertEqual(len(archived_actions), 1)
        self.assertEqual(metadata["stage_log"][-1]["to"], "between")
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.manager.disconnect_calls, 0)

    def test_between_starts_next_batch_without_reconnecting_and_is_stoppable(self) -> None:
        self._start()
        self.window._on_event_requested("CHARGE", 0.0, "入豆")
        self._sample(1.0)
        self._drop()
        self._confirm_archive()
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)

        self.window._begin_monitoring()
        self.assertEqual(self.window.workflow.state, RoastFlowState.READY_TO_CHARGE)
        self.window._on_event_requested("CHARGE", 0.0, "入豆")
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertEqual(self.window.workflow.batch_number, 2)
        self.assertEqual(len(self.manager.connect_calls), 1)
        self.assertEqual(self.window.samples, [])
        self.assertEqual([item["event_type"] for item in self.window.events], ["CHARGE"])
        self.assertEqual(len(self.window.actions), 1)

        self._sample(2.0)
        self.window._end_monitoring()
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertEqual(self.manager.disconnect_calls, 0)
        self._drop()
        self.window.db.archive_session.return_value = SessionArchiveResult(
            charge_id=21, batch_id=22, charge_number=2
        )
        self._confirm_archive()
        self.window._end_monitoring()
        self.assertEqual(self.window.workflow.stage, RoastStage.IDLE)
        self.assertFalse(self.window.session_active)
        self.assertEqual(self.manager.disconnect_calls, 1)

    def test_automatic_tp_does_not_run_when_disabled(self) -> None:
        candidate = {"time_s": 1.0, "it": 120.0, "et": 110.0, "bt": 100.0, "bt_ror": 0.0}
        with patch("app.ui.main_window.detect_tp", return_value=candidate) as detect:
            self._start()
            self.window._on_event_requested("CHARGE", 0.0, "入豆")
            self._sample(1.0)
            detect.assert_not_called()
            self.assertNotIn("TP", self.window.workflow.recorded_event_types)

    def test_automatic_tp_runs_when_enabled_for_the_batch(self) -> None:
        candidate = {"time_s": 1.0, "it": 120.0, "et": 110.0, "bt": 100.0, "bt_ror": 0.0}
        self.window.configuration_state.stage(auto_tp_enabled=True)
        with patch("app.ui.main_window.detect_tp", return_value=candidate) as detect:
            self._start()
            self.window._on_event_requested("CHARGE", 0.0, "入豆")
            self._sample(1.0)
            detect.assert_called_once()
        self.assertIn("TP", self.window.workflow.recorded_event_types)
        self.assertEqual(self.window.events[-1]["event_type"], "TP")


if __name__ == "__main__":
    unittest.main()
