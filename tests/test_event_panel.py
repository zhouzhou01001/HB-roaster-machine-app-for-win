import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from app.ui.event_panel import EVENT_BUTTONS, EventPanel


class EventPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_milestone_records_cancels_and_records_again(self):
        panel = EventPanel()
        panel.set_current_time(lambda: 42.0)
        panel.set_session_active(True)
        requested, cancelled = [], []
        panel.event_requested.connect(lambda kind, when, note: requested.append((kind, when, note)))
        panel.event_cancelled.connect(cancelled.append)

        panel._buttons["YELLOW"].click()
        self.assertEqual(requested, [("YELLOW", 42.0, "")])
        panel._buttons["YELLOW"].click()
        self.assertEqual(cancelled, ["YELLOW"])
        panel.set_current_time(lambda: 55.0)
        panel._buttons["YELLOW"].click()
        self.assertEqual(requested, [("YELLOW", 42.0, ""), ("YELLOW", 55.0, "")])

    def test_milestone_buttons_stay_available_after_recording(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_current_time(lambda: 42.0)
        panel.event_requested.connect(lambda *_: None)
        panel._buttons["FC_START"].click()
        self.assertTrue(panel._buttons["FC_START"].isChecked())
        self.assertTrue(panel._buttons["FC_START"].isEnabled())

    def test_milestone_button_shows_recorded_time(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_current_time(lambda: 202.0)
        panel.set_events([])
        panel._buttons["YELLOW"].click()

        self.assertEqual(panel._buttons["YELLOW"].text(), "转黄 03:22")

    def test_milestone_button_restores_label_after_cancel_and_re_record(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_events([{"event_type": "FC_START", "time_s": 9.0}])
        self.assertEqual(panel._buttons["FC_START"].text(), "一爆开始 00:09")

        panel.set_events([])
        self.assertEqual(panel._buttons["FC_START"].text(), "一爆开始")

        panel.set_events([{"event_type": "FC_START", "time_s": 15.0}])
        self.assertEqual(panel._buttons["FC_START"].text(), "一爆开始 00:15")

    def test_six_milestone_buttons_support_record_cancel_and_re_record(self):
        panel = EventPanel()
        panel.set_session_active(True)
        marker = {"time_s": 0.0}
        panel.set_current_time(lambda: marker["time_s"])
        requested, cancelled = [], []
        panel.event_requested.connect(lambda kind, when, note: requested.append((kind, when, note)))
        panel.event_cancelled.connect(cancelled.append)

        for event_type in [name for name, _ in EVENT_BUTTONS if name not in ("CHARGE", "DROP")]:
            requested.clear()
            cancelled.clear()
            marker["time_s"] = 10.0
            panel._buttons[event_type].click()
            self.assertEqual(requested, [(event_type, 10.0, "")])
            panel.set_events([{"event_type": event_type, "time_s": 10.0}])
            rendered = [panel.event_list.item(index).text() for index in range(panel.event_list.count())]
            self.assertEqual(rendered, [f"10.0 秒  |  {panel._names[event_type]}"])
            self.assertTrue(panel._buttons[event_type].isChecked())
            self.assertTrue(panel._buttons[event_type].isEnabled())

            panel._buttons[event_type].click()
            self.assertEqual(cancelled, [event_type])
            marker["time_s"] = 20.0
            panel._buttons[event_type].click()
            self.assertEqual(requested, [(event_type, 10.0, ""), (event_type, 20.0, "")])

    def test_charge_is_only_available_while_waiting_and_locks_after_recording(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_ready_to_charge(True)
        panel.set_current_time(lambda: 0.0)
        requested = []
        panel.event_requested.connect(lambda kind, when, note: requested.append((kind, when, note)))

        self.assertTrue(panel._buttons["CHARGE"].isEnabled())
        self.assertFalse(panel._buttons["YELLOW"].isEnabled())
        panel._buttons["CHARGE"].click()
        self.assertEqual(requested, [("CHARGE", 0.0, "")])
        panel.set_events([{"event_type": "CHARGE", "time_s": 0.0}])
        self.assertFalse(panel._buttons["CHARGE"].isEnabled())

    def test_drop_is_immediate_and_cannot_be_cancelled(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_current_time(lambda: 42.0)
        requested, cancelled = [], []
        panel.event_requested.connect(lambda kind, when, note: requested.append((kind, when, note)))
        panel.event_cancelled.connect(cancelled.append)
        panel._buttons["DROP"].click()
        self.assertEqual(requested, [("DROP", 42.0, "")])
        self.assertEqual(cancelled, [])
        self.assertFalse(panel._buttons["DROP"].isEnabled())
        panel._buttons["DROP"].click()
        self.assertEqual(requested, [("DROP", 42.0, "")])
        self.assertEqual(cancelled, [])

    def test_out_of_order_events_are_displayed_in_time_order(self):
        panel = EventPanel()
        panel.set_session_active(True)
        panel.set_events(
            [
                {"event_type": "DROP", "time_s": 30.0},
                {"event_type": "YELLOW", "time_s": 10.0},
                {"event_type": "CHARGE", "time_s": 12.0},
            ]
        )
        rendered = [panel.event_list.item(index).text() for index in range(panel.event_list.count())]
        self.assertIn("10.0", rendered[0])
        self.assertIn("12.0", rendered[1])
        self.assertIn("30.0", rendered[2])


if __name__ == "__main__":
    unittest.main()
