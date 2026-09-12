from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.roast.workflow import RoastFlowState
from app.ui.main_window import MainWindow


class FakeSerialManager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self, *_args, **_kwargs) -> None:
        self.connect_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class ThreeStageRoastFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.manager = FakeSerialManager()
        self.serial_patch = patch("app.ui.main_window.SerialManager", return_value=self.manager)
        self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        root = Path(self.workspace.name)
        self.window = MainWindow(root / "flow.hbroast", root / "channels.json")
        self.window.curve.set_data = lambda *_args, **_kwargs: None
        self.window.curve.set_events = lambda *_args, **_kwargs: None
        self.addCleanup(self.window.close)
        self.window.selected_source = "SIMULATION"
        self.window.selected_profile = "SIMULATION"
        self.window.selected_baudrate = 0

    def emit_sample(self, time_s: float, bt: float = 100.0) -> None:
        self.manager.sample_received.emit(
            {
                "time_s": time_s,
                "CH1": bt + 20.0,
                "CH2": bt + 10.0,
                "CH3": bt,
                "CH4": bt + 5.0,
                "source": "SIMULATION",
            }
        )
        self.app.processEvents()

    def test_collect_prepare_charge_drop_and_continue_live_display(self) -> None:
        self.window._begin_monitoring()
        self.assertEqual(self.window.workflow.state, RoastFlowState.CONNECTED)
        self.assertEqual(self.manager.connect_calls, 1)
        self.assertFalse(self.window.start_next_batch_button.isEnabled())

        self.emit_sample(10.0)
        self.assertTrue(self.window.start_next_batch_button.isEnabled())
        self.window._begin_monitoring()
        self.assertEqual(self.window.workflow.state, RoastFlowState.READY_TO_CHARGE)
        self.assertTrue(self.window.event_panel._buttons["CHARGE"].isEnabled())
        self.assertFalse(self.window.event_panel._buttons["YELLOW"].isEnabled())
        self.assertEqual(self.manager.connect_calls, 1)

        self.emit_sample(12.0, 101.0)
        self.assertGreater(len(self.window.samples), 0)
        self.window._on_event_requested("CHARGE", 12.0, "入豆")
        self.assertEqual(self.window.workflow.state, RoastFlowState.ROASTING)
        self.assertEqual(self.window.events[0]["event_type"], "CHARGE")
        self.assertEqual(self.window.events[0]["time_s"], 0.0)
        self.assertEqual(self.window.samples, [])
        self.assertFalse(self.window.event_panel._buttons["CHARGE"].isEnabled())

        self.emit_sample(13.0, 102.0)
        self.assertAlmostEqual(self.window.samples[0]["time_s"], 1.0)
        self.emit_sample(14.0, 103.0)
        for widget in (
            self.window.metric_it.sub_value_widget,
            self.window.metric_exhaust.sub_value_widget,
        ):
            self.assertNotRegex(widget.text(), r"\d+\.\d+")
        self.window._on_event_requested("YELLOW", 1.0, "")
        self.assertEqual(self.window.events[-1]["time_s"], 1.0)
        self.window._on_event_requested("DROP", 2.0, "")
        self.assertEqual(self.window.workflow.state, RoastFlowState.FINISHED)
        formal_count = len(self.window.samples)
        self.emit_sample(15.0, 103.0)
        self.assertEqual(len(self.window.samples), formal_count)
        self.assertEqual(self.manager.disconnect_calls, 0)

    def test_duplicate_collection_and_charge_are_rejected(self) -> None:
        self.window._begin_monitoring()
        self.window._begin_monitoring()
        self.assertEqual(self.manager.connect_calls, 1)
        self.emit_sample(1.0)
        self.window._begin_monitoring()
        self.window._on_event_requested("CHARGE", 1.0, "")
        event_count = len(self.window.events)
        self.window._on_event_requested("CHARGE", 1.0, "")
        self.assertEqual(len(self.window.events), event_count)

    def test_manual_control_sliders_keep_full_width_in_compact_mode(self) -> None:
        panel = self.window.action_panel
        panel.set_compact_mode(True)
        for card, slider in zip(
            panel._control_cards,
            (panel.gas_slider, panel.damper_slider, panel.rpm_slider),
        ):
            self.assertEqual(card.layout().indexOf(slider), 2)
            self.assertGreaterEqual(slider.minimumWidth(), 180)
            self.assertEqual(
                slider.sizePolicy().horizontalPolicy(),
                QtWidgets.QSizePolicy.Policy.Expanding,
            )


if __name__ == "__main__":
    unittest.main()
