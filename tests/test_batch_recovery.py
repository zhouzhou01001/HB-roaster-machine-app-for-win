import sqlite3
from unittest.mock import patch

import pytest
from PySide6 import QtCore, QtWidgets

from app.ui.main_window import MainWindow
from app.roast.workflow import RoastFlowState, WorkflowError


class Manager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.connect_calls = []

    def connect(self, port, baudrate=9600, profile=None):
        self.connect_calls.append((port, baudrate, profile))
        self.state_changed.emit(True)

    def start_simulation(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass


@pytest.fixture
def window(tmp_path, qt_application):
    manager = Manager()
    with patch("app.ui.main_window.SerialManager", return_value=manager):
        view = MainWindow(tmp_path / "recovery.hbroast", tmp_path / "channels.json")
    view.resize(1280, 720)
    view.show()
    qt_application.processEvents()
    view.configuration_state.stage(auto_tp_enabled=False)
    view.selected_source = "SIMULATION"
    view.start_monitor_button.click()
    assert manager.connect_calls == [("SIMULATION", view.selected_baudrate, view.selected_profile)]
    manager.sample_received.emit({"time_s": 0, "CH1": 150, "CH2": 140, "CH3": 130, "CH4": 120})
    view.start_next_batch_button.click()
    view.event_panel._buttons["CHARGE"].click()
    yield view
    if view._preview_dialog is not None:
        view._preview_dialog.reject()
    view.close()


def finish(view, with_samples=True):
    if with_samples:
        view.manager.sample_received.emit({"time_s": 1, "CH1": 150, "CH2": 140, "CH3": 130, "CH4": 120})
    view.event_panel._buttons["DROP"].click()
    view.reset_batch_button.click()
    QtWidgets.QApplication.processEvents()
    return view._preview_dialog


def test_empty_batch_has_visible_error_and_confirmed_recovery(window):
    dialog = finish(window, False)
    assert dialog.archive_error_label.isVisible()
    assert "没有入豆后的有效采样" in dialog.archive_error_label.text()
    assert not dialog.confirm_button.isEnabled()
    assert dialog.discard_empty_button.isVisible()
    with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.No):
        dialog.discard_empty_button.click()
    assert window.workflow.state is RoastFlowState.FINISHED
    assert window.events
    with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
        dialog.discard_empty_button.click()
    assert window.db.list_charges() == []
    assert not window.events and not window.actions
    assert window.start_next_batch_button.isEnabled()
    window.start_next_batch_button.click()
    assert window.workflow.state is RoastFlowState.READY_TO_CHARGE


def test_busy_save_remains_visible_and_retry_archives_only_once(window):
    dialog = finish(window)
    original = window.session_buffer.snapshot()
    with patch.object(window.db, "archive_session", side_effect=sqlite3.OperationalError("database is locked")):
        dialog.confirm_button.click()
    assert dialog.isVisible() and dialog.archive_error_label.isVisible()
    assert "数据库正被占用" in dialog.archive_error_label.text()
    assert window.session_buffer.snapshot() == original
    assert not window.start_next_batch_button.isEnabled()
    assert window.db.list_charges() == []
    dialog.confirm_button.click()
    dialog.confirm_button.click()
    assert len(window.db.list_charges()) == 1
    assert window.start_next_batch_button.isEnabled()
    window.start_next_batch_button.click()
    assert window.workflow.batch_number == 2
    assert window.workflow.state is RoastFlowState.READY_TO_CHARGE


def test_disconnect_explains_block_and_fresh_sample_restores_next_batch(window):
    dialog = finish(window)
    dialog.confirm_button.click()
    window.manager.state_changed.emit(False)
    assert not window.start_next_batch_button.isEnabled()
    assert "等待有效设备温度" in window.start_next_batch_button.toolTip()
    window.manager.sample_received.emit({"time_s": 2, "CH1": 150, "CH2": 140, "CH3": 130, "CH4": 120})
    assert window.start_next_batch_button.isEnabled()
    assert len(window.db.list_charges()) == 1


def test_nonempty_batch_cannot_use_empty_discard(window):
    finish(window)
    with pytest.raises(WorkflowError):
        window.workflow.discard_empty_batch(len(window.samples))
    assert window.workflow.state is RoastFlowState.FINISHED
    assert window.samples


def test_two_batches_use_one_connection_and_separate_records(window):
    first = finish(window)
    first.confirm_button.click()
    assert window.start_next_batch_button.isEnabled()
    window.start_next_batch_button.click()
    window.event_panel._buttons["CHARGE"].click()
    assert window.workflow.batch_number == 2
    assert window.samples == []
    assert [event["event_type"] for event in window.events] == ["CHARGE"]
    window.manager.sample_received.emit({"time_s": 2, "CH1": 151, "CH2": 141, "CH3": 131, "CH4": 121})
    second = finish(window, False)
    second.confirm_button.click()
    assert len(window.db.list_charges()) == 2
    assert len(window.manager.connect_calls) == 1
    assert window.start_next_batch_button.isEnabled()
