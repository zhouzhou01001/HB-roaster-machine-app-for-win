import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.database.database import RoastDatabase
from app.roast.workflow import RoastFlowState, RoastStage
from app.ui.main_window import MainWindow


class FakeSerialManager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.connect_calls: list[tuple[str, int]] = []
        self.disconnect_calls = 0
        self.start_simulation_calls = 0

    def connect(self, port: str, baudrate: int = 9600) -> None:
        self.connect_calls.append((port, baudrate))

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def start_simulation(self, interval_ms: int = 800) -> None:
        self.start_simulation_calls += 1


class MainWindowLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self._workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self._workspace.cleanup)
        self.db_path = Path(self._workspace.name) / "session_test.hbroast"
        self.channel_map = Path(self._workspace.name) / "channel.json"

        self.fake_manager = FakeSerialManager()
        self.serial_patch = patch("app.ui.main_window.SerialManager", return_value=self.fake_manager)
        self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)

        self.window = MainWindow(
            db_path=self.db_path,
            channel_mapping_path=self.channel_map,
        )
        self.window.configuration_state.stage(auto_tp_enabled=False)
        self._disable_chart_updates()
        self.addCleanup(self._cleanup_window)

    def tearDown(self) -> None:
        # Keep timers idle for stable test shutdown on CI.
        self.window._clear_connection_retry()

    def _cleanup_window(self) -> None:
        if self.window is None:
            return
        if self.window.session_active:
            self.window._end_monitoring()
        self.fake_manager.disconnect()
        self.window.manager = self.fake_manager
        self.window.close()

    def _disable_chart_updates(self) -> None:
        self.window.curve.set_data = lambda *args, **kwargs: None
        self.window.curve.set_events = lambda *args, **kwargs: None
        self.window.curve.set_targets = lambda *args, **kwargs: None
        self.window.curve.set_background_profile = lambda *args, **kwargs: None

    def _start_simulation_monitoring(self) -> None:
        self.window.selected_source = "SIMULATION"
        self.window._begin_monitoring()
        self._emit_sample(0.0)
        self.window._begin_monitoring()
        self.window._on_event_requested("CHARGE", 0.0, "")
        self.window.events.clear()
        self.window.workflow.recorded_event_types.discard("CHARGE")
        self.window.event_panel.set_events([])

    def _emit_sample(self, time_s: float, it: float = 100.0, et: float = 110.0, bt: float = 90.0) -> None:
        self.fake_manager.sample_received.emit(
            {
                "time_s": time_s,
                "CH1": it,
                "CH2": et,
                "CH3": bt,
            }
        )

    def _finish_current_batch(self, drop_time_s: float | None = None) -> None:
        if self.window.workflow.stage is RoastStage.ROASTING:
            self.window._on_event_requested("DROP", drop_time_s or self.window._get_time_s(), "")
        self.assertEqual(self.window.workflow.stage, RoastStage.AWAIT_END)
        self.window._request_finish_batch()
        self.app.processEvents()
        self.assertIsNotNone(self.window._preview_dialog)
        self.window._preview_dialog.confirm_button.click()
        self.app.processEvents()
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN, self.window.statusBar().currentMessage())

    def _seed_history_database(self) -> tuple[Path, int]:
        seed_path = Path(self._workspace.name) / "history.hbroast"
        seed_db = RoastDatabase(seed_path)
        first = seed_db.create_batch(coffee_name="batch-1")
        seed_db.insert_sample({"time_s": 10.0, "it": 120.0, "et": 130.0, "bt": 95.0}, roast_id=first)
        seed_db.insert_event(first, 10.0, "CHARGE", 95.0)
        seed_db.insert_manual_action(first, 10.0, 2.5, 12.0, 100.0, "first")
        second = seed_db.create_batch(coffee_name="batch-2")
        seed_db.insert_sample({"time_s": 12.0, "it": 121.0, "et": 132.0, "bt": 96.0}, roast_id=second)
        seed_db.insert_event(second, 12.0, "YELLOW", 96.0)
        seed_db.insert_manual_action(second, 12.0, 3.1, 15.0, 120.0, "second")
        return seed_path, second

    def _seed_empty_batch_database(self) -> tuple[Path, int]:
        seed_path = Path(self._workspace.name) / "empty.hbroast"
        seed_db = RoastDatabase(seed_path)
        batch_id = seed_db.create_batch(coffee_name="empty-batch")
        return seed_path, batch_id

    def test_new_monitoring_batch_is_isolated(self) -> None:
        self._start_simulation_monitoring()
        self._emit_sample(0.0)
        self._emit_sample(1.0)
        first_batch = self.window._batch_id

        self.assertEqual(len(self.window.samples), 2)
        self.assertEqual(self.window.db.load_samples(first_batch), [])
        self.assertTrue(self.window.session_active)

        self.window._end_monitoring()
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self._finish_current_batch(2.0)
        self.assertEqual(self.window.db.list_charges()[0]["sample_count"], 2)

        self.window._begin_monitoring()
        self.window._on_event_requested("CHARGE", 0.0, "")
        second_batch = self.window._batch_id
        self.assertNotEqual(second_batch, first_batch)
        self.assertEqual(self.window.samples, [])
        self.assertEqual([item["event_type"] for item in self.window.events], ["CHARGE"])
        self.assertEqual(len(self.window.actions), 1)

        # Acquisition continues across batches; only the roast axis resets.
        self._emit_sample(2.0)
        self.assertEqual(len(self.window.samples), 1)
        self.assertEqual(len(self.window.db.list_charges()), 1)
        self._finish_current_batch(1.0)

        charges = self.window.db.list_charges()
        self.assertEqual(len(charges), 2)
        self.assertEqual(sorted(item["sample_count"] for item in charges), [1, 2])
        self.assertEqual(len(self.window.db.list_batches()), 2)

    def test_session_status_controls_and_clear_background_stay_consistent(self) -> None:
        self.assertFalse(self.window.session_active)
        self.assertTrue(self.window.start_monitor_button.isEnabled())
        self.assertFalse(self.window.stop_monitor_button.isEnabled())
        self.assertTrue(self.window.action_panel.isEnabled() is False)
        self.assertTrue(self.window.act_start.isEnabled())
        self.assertFalse(self.window.act_stop.isEnabled())

        self.window.background_profile_name = "seed-profile"
        self.window._clear_background_curve()
        self.assertEqual(self.window.background_profile_name, "")

        self._start_simulation_monitoring()
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertFalse(self.window.stop_monitor_button.isEnabled())
        self.assertFalse(self.window.start_monitor_button.isEnabled())
        self.assertTrue(self.window.action_panel.isEnabled())

        self._emit_sample(1.0)
        self.window._clear_background_curve()
        self.assertEqual(self.window.background_profile_name, "")
        self.assertTrue(self.window.session_active)
        self.assertFalse(self.window.stop_monitor_button.isEnabled())

        self.window._end_monitoring()
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self._finish_current_batch()
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)
        self.assertEqual(self.window.workflow.state, RoastFlowState.CONNECTED)
        self.assertFalse(self.window.start_monitor_button.isEnabled())
        self.assertIn("设备已连接", self.window.start_monitor_button.text())
        self.assertFalse(self.window.start_next_batch_button.isHidden())
        self.assertTrue(self.window.start_next_batch_button.isEnabled())
        self.assertTrue(self.window.stop_monitor_button.isEnabled())
        self.window._end_monitoring()
        self.assertFalse(self.window.session_active)
        self.assertTrue(self.window.start_monitor_button.isEnabled())
        self.assertFalse(self.window.stop_monitor_button.isEnabled())
        self.assertFalse(self.window.session_active)

    def test_open_batch_keeps_session_inactive_and_loads_selected_history(self) -> None:
        seed_path, latest_batch_id = self._seed_history_database()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(seed_path), "")):
            self.window._open_batch()

        self.assertFalse(self.window.session_active)
        self.assertEqual(self.window._batch_id, latest_batch_id)
        self.assertEqual(len(self.window.samples), 1)
        self.assertEqual(len(self.window.events), 1)
        self.assertEqual(len(self.window.actions), 1)
        self.assertTrue(self.fake_manager.disconnect_calls >= 1)
        self.assertTrue(self.window.start_monitor_button.isEnabled())
        self.assertFalse(self.window.stop_monitor_button.isEnabled())
        self.assertFalse(self.window.action_panel.isEnabled())

    def test_reconnect_retry_limit_sets_inactive_with_warning_and_keeps_source(self) -> None:
        self.window.selected_source = "COM_FAKE"
        self.window._set_session_active(True)
        self.window._clear_connection_retry()
        self.assertFalse(self.window._reconnect_timer.isActive())
        self.assertEqual(self.window._reconnect_attempts, 0)

        for index in range(1, self.window._max_reconnect_attempts + 1):
            self.window._attempt_reconnect()
            self.assertEqual(self.window._reconnect_attempts, index)
            self.assertIn(("COM_FAKE", self.window.selected_baudrate), self.window.manager.connect_calls)

        self.assertEqual(self.window.selected_source, "COM_FAKE")
        self.assertEqual(self.window.manager.disconnect_calls, 0)
        self.assertFalse(self.window._reconnect_timer.isActive())

        self.window._schedule_reconnect()
        self.assertEqual(self.window.selected_source, "COM_FAKE")
        self.assertEqual(self.window.manager.disconnect_calls, 1)
        self.assertFalse(self.window.session_active)
        self.assertIn("监测已中止", self.window.session_status_label.text())
        self.assertFalse(self.window._reconnect_timer.isActive())
        self.assertFalse(self.window._reconnect_timer.isActive())

    def test_reconnect_failure_keeps_batch_and_never_switches_to_simulation(self) -> None:
        seed_path, batch_id = self._seed_empty_batch_database()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(seed_path), "")):
            self.window._open_batch()
        self.assertEqual(self.window._batch_id, batch_id)
        self.assertEqual(self.window.samples, [])

        self.window.selected_source = "COM_FAKE"
        self.window._batch_prepared_for_monitoring = True
        self.window._begin_monitoring()
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertEqual(self.window.samples, [])
        for _ in range(self.window._max_reconnect_attempts):
            self.window._attempt_reconnect()
        self.window._schedule_reconnect()
        self.assertFalse(self.window.session_active)
        self.assertEqual(self.window.selected_source, "COM_FAKE")
        self.assertIn("监测已中止", self.window.current_data_label.text())
        pre_count = len(self.window.samples)
        self._emit_sample(1.0)
        post_count = len(self.window.samples)

        self.assertEqual(post_count, pre_count)
        self.assertEqual(self.window._reconnect_attempts, self.window._max_reconnect_attempts)

    def test_reconnect_failure_only_allows_sampling_after_explicit_simulation_source_change(self) -> None:
        seed_path, batch_id = self._seed_empty_batch_database()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(seed_path), "")):
            self.window._open_batch()
        self.window._batch_prepared_for_monitoring = True
        self.window.selected_source = "COM_FAKE"
        self.window._begin_monitoring()
        for _ in range(self.window._max_reconnect_attempts):
            self.window._attempt_reconnect()
        self.window._schedule_reconnect()
        self.assertEqual(self.window.selected_source, "COM_FAKE")
        self.assertFalse(self.window.session_active)

        self.window.selected_source = "SIMULATION"
        self.window._begin_monitoring()
        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.ROASTING)
        self.assertEqual(self.window.samples, [])
        self._emit_sample(1.0)
        self.assertEqual(self.window.samples, [])
        self.assertEqual(self.window.workflow.state, RoastFlowState.CONNECTED)
        self.assertTrue(self.window._has_valid_device_sample)
        self.assertEqual(self.window.db.load_samples(batch_id), [])

    def test_preview_confirmation_archives_exactly_one_charge_and_batch(self) -> None:
        self.assertEqual(self.window.db.list_charges(), [])
        self.assertEqual(self.window.db.list_batches(), [])

        self._start_simulation_monitoring()
        self._emit_sample(1.0)
        self.window._on_event_requested("DROP", 2.0, "")
        self.window._request_finish_batch()
        self.app.processEvents()
        self.assertEqual(self.window.db.list_charges(), [])
        self.assertEqual(self.window.db.list_batches(), [])

        self.window._preview_dialog.confirm_button.click()
        self.app.processEvents()
        self.assertEqual(len(self.window.db.list_charges()), 1)
        self.assertEqual(len(self.window.db.list_batches()), 1)
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)

        self._start_simulation_monitoring()
        self._emit_sample(1.0, bt=91.0)
        self._finish_current_batch(2.0)
        self.assertEqual(len(self.window.db.list_charges()), 2)
        self.assertEqual(len(self.window.db.list_batches()), 2)

    def test_first_sample_with_none_or_nan_or_inf_ror_does_not_raise_typeerror(self) -> None:
        self.window.selected_source = "SIMULATION"
        with patch(
            "app.ui.main_window.calculate_ror",
            return_value={
                "it_ror": float("nan"),
                "IT_RoR": float("nan"),
                "et_ror": float("inf"),
                "ET_RoR": float("inf"),
                "bt_ror": None,
                "BT_RoR": None,
            },
        ):
            self.window._begin_monitoring()
            try:
                self._emit_sample(0.0, it=100.0, et=110.0, bt=90.0)
            except TypeError as exc:
                self.fail(f"first sample refresh should not throw TypeError: {exc}")

        self.assertEqual(self.window.samples, [])
        self.assertTrue(self.window._has_valid_device_sample)
        self.assertEqual(self.window.db.load_samples(self.window._batch_id), [])

    def test_invalid_sample_is_rejected_and_does_not_pollute_batch(self) -> None:
        self._start_simulation_monitoring()
        self._emit_sample(0.0, it=100.0, et=110.0, bt=90.0)
        before = len(self.window.samples)

        self._emit_sample(1.0, it=1000.0, et=1000.0, bt=1000.0)
        after_rejected = len(self.window.samples)
        self.assertEqual(before, after_rejected)

        self._emit_sample(2.0, it=102.0, et=112.0, bt=92.0)
        after_recovered = len(self.window.samples)
        self.assertEqual(after_recovered, before + 1)
        self.assertEqual(self.window.db.load_samples(self.window._batch_id), [])

    def test_milestone_cycle_updates_memory_and_curve_event_state(self) -> None:
        self._start_simulation_monitoring()
        self._emit_sample(10.0, it=101.0, et=111.0, bt=91.0)
        captured_set_events: list[list[dict]] = []

        original_set_events = self.window.curve.set_events
        self.addCleanup(setattr, self.window.curve, "set_events", original_set_events)
        self.window.curve.set_events = lambda events: captured_set_events.append([dict(row) for row in events])

        fc_start_btn = self.window.event_panel._buttons["FC_START"]
        fc_start_btn.click()
        self.assertEqual(len(captured_set_events[-1]), 1)
        self.assertEqual(captured_set_events[-1][0]["event_type"], "FC_START")
        self.assertEqual(captured_set_events[-1][0]["time_s"], 10.0)
        self.assertEqual(len(self.window.events), 1)
        self.assertEqual(self.window.events[0]["time_s"], 10.0)
        self.assertEqual(self.window.db.load_events(self.window._batch_id), [])
        self.assertEqual(self.window.event_panel.event_list.count(), 1)
        self.assertIn("10.0", self.window.event_panel.event_list.item(0).text())

        fc_start_btn.click()
        self.assertEqual(self.window.events, [])
        self.assertEqual(self.window.event_panel.event_list.count(), 0)
        self.assertEqual(len(captured_set_events[-1]), 0)

        self._emit_sample(20.0, it=102.0, et=112.0, bt=92.0)
        fc_start_btn.click()
        self.assertEqual(len(self.window.events), 1)
        self.assertEqual(self.window.events[0]["time_s"], 20.0)
        self.assertEqual(self.window.db.load_events(self.window._batch_id), [])
        self.assertIn("20.0", self.window.event_panel.event_list.item(0).text())

    def test_drop_enters_await_end_keeps_sampling_and_archives_only_after_confirmation(self) -> None:
        self._start_simulation_monitoring()
        self._emit_sample(10.0, it=101.0, et=111.0, bt=91.0)
        before_samples = len(self.window.samples)

        drop_btn = self.window.event_panel._buttons["DROP"]
        drop_btn.click()

        self.assertTrue(self.window.session_active)
        self.assertEqual(self.window.workflow.stage, RoastStage.AWAIT_END)
        self.assertFalse(self.window.start_monitor_button.isEnabled())
        self.assertFalse(self.window.stop_monitor_button.isEnabled())
        self.assertFalse(self.window.action_panel.isEnabled())
        self.assertFalse(self.window.event_panel._buttons["YELLOW"].isEnabled())
        self.assertFalse(drop_btn.isEnabled())
        self.assertIn("已排豆", self.window.session_status_label.text())

        self._emit_sample(20.0, it=102.0, et=112.0, bt=92.0)
        self.assertEqual(len(self.window.samples), before_samples)
        self.assertEqual(self.window.db.list_charges(), [])
        self.assertEqual(self.window.db.list_batches(), [])

        self.window._end_monitoring()
        self.assertEqual(self.window.workflow.stage, RoastStage.AWAIT_END)
        self.assertEqual(self.fake_manager.disconnect_calls, 0)
        self.window._request_finish_batch()
        self.app.processEvents()
        self.assertEqual(self.window.db.list_charges(), [])
        self.window._preview_dialog.confirm_button.click()
        self.app.processEvents()

        charges = self.window.db.list_charges()
        self.assertEqual(len(charges), 1)
        self.assertEqual(charges[0]["sample_count"], before_samples)
        self.assertEqual(charges[0]["event_count"], 1)
        self.assertEqual(len(self.window.db.list_batches()), 1)
        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)
        self.assertTrue(self.window.session_active)

    def test_main_window_without_dual_side_scroll_areas(self) -> None:
        self.assertEqual(
            len(self.window.findChildren(QtWidgets.QScrollArea)),
            0,
            "曲线区与操作按钮区不应依赖左右独立滚动区",
        )

    def test_supported_window_sizes_keep_charts_and_controls_visible(self) -> None:
        self.window.show()
        for width, height in (
            (1280, 720),
            (1920, 1080),
            (2560, 1440),
            (2880, 1620),
            (2880, 1800),
            (3840, 2160),
        ):
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertLessEqual(self.window.minimumSizeHint().width(), width)
            self.assertLessEqual(self.window.minimumSizeHint().height(), height)
            self.assertLessEqual(self.window.findChild(QtWidgets.QToolBar).sizeHint().width(), self.window.width())
            self.assertGreater(self.window.curve.temp_plot.viewport().width(), 0)
            self.assertGreater(self.window.curve.temp_plot.viewport().height(), 0)
            self.assertGreater(self.window.curve.temp_plot.viewport().width(), 0)
            self.assertGreater(self.window.curve.temp_plot.viewport().height(), 0)
            self.assertGreaterEqual(self.window.right_host.width(), self.window.right_host.minimumWidth())
            for button in (*self.window.event_panel._buttons.values(), self.window.start_monitor_button, self.window.stop_monitor_button):
                self.assertGreater(button.width(), 0)
                self.assertGreater(button.height(), 0)
