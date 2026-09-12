import threading
import time
import types
import unittest
from unittest.mock import patch
from PySide6 import QtCore

from app.device.serial_manager import SerialManager


class SerialManagerRegressionTests(unittest.TestCase):
    def test_read_loop_exception_does_not_join_current_thread(self) -> None:
        error_messages: list[str] = []
        state_changes: list[bool] = []
        thread_calls: list[tuple[bool, str]] = []
        thread_exceptions: list[tuple[str, str]] = []

        class FakeSerialPort:
            def write(self, *_args, **_kwargs) -> None:
                return None

            def readline(self) -> bytes:
                raise RuntimeError("fake serial disconnect")

            def close(self) -> None:
                return None

        fake_serial = types.SimpleNamespace()
        fake_ports = []

        def serial_for_url(*_args, **_kwargs):
            serial = FakeSerialPort()
            fake_ports.append(serial)
            return serial

        fake_serial.serial_for_url = serial_for_url

        def comports():
            return []

        fake_serial.tools = types.SimpleNamespace(list_ports=types.SimpleNamespace(comports=comports))

        def capture_state(state: bool) -> None:
            state_changes.append(state)

        def capture_error(message: str) -> None:
            error_messages.append(message)

        original_join = threading.Thread.join
        original_hook = threading.excepthook

        def guarded_join(self_thread: threading.Thread, timeout: float | None = None) -> None:
            same_thread = self_thread is threading.current_thread()
            if same_thread:
                thread_calls.append((True, "same-thread-join"))
                raise RuntimeError("cannot join current thread")
            thread_calls.append((False, "cross-thread-join"))
            return original_join(self_thread, timeout=timeout)

        def capture_hook(args):  # pragma: no cover - exercised in regression behavior
            thread_exceptions.append((args.exc_type.__name__, str(args.exc_value)))

        try:
            with patch("app.device.serial_manager.serial", fake_serial), patch(
                "threading.Thread.join", guarded_join
            ):
                with patch("threading.excepthook", capture_hook):
                    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
                    manager = SerialManager()
                    manager.state_changed.connect(capture_state)
                    manager.error.connect(capture_error)
                    manager.connect("COM_FAKE")

                    deadline = time.time() + 2.0
                    while time.time() < deadline and False not in state_changes:
                        app.processEvents()
                        time.sleep(0.05)

                    self.assertIn(True, state_changes)
                    self.assertIn(False, state_changes)
                    self.assertEqual(state_changes.count(False), 1)
                    self.assertTrue(any("Serial read error" in message for message in error_messages))
                    self.assertFalse(any("cannot join current thread" in message for _, message in thread_exceptions))
                    self.assertFalse(any(is_same for is_same, _ in thread_calls))

                    manager.disconnect()
                    self.assertEqual(fake_ports[-1].__class__.__name__, "FakeSerialPort")
        finally:
            threading.Thread.join = original_join
            threading.excepthook = original_hook

        # Ensure cleanup didn't alter state transitions.
        self.assertEqual(state_changes.count(True), 1)
