import os
import pathlib
import sqlite3
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.ui.archive_summary_panel import ArchiveSummaryPanel
from app.ui.workflow_panel import WorkflowPanel


class WorkflowPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_all_states_render_one_active_row_and_counters(self) -> None:
        panel = WorkflowPanel()
        expected = {
            "idle": "待采集",
            "roasting": "烘焙中",
            "await_end": "待结束",
            "between": "锅次间隙",
        }

        for index, (state, label) in enumerate(expected.items(), start=1):
            panel.set_state(state, total_charges=index + 2, current_charge=index)
            self.assertEqual(panel.current_state, state)
            self.assertIn(label, panel.status_badge.text())
            self.assertEqual(panel.current_charge_label.text(), f"第 {index} 锅")
            self.assertEqual(panel.total_charge_label.text(), f"{index + 2} 锅")
            active = [code for code, row in panel.state_rows.items() if row.property("active")]
            self.assertEqual(active, [state])
            self.assertEqual(panel.status_badge.property("workflowState"), state)

    def test_state_validation_and_idle_without_current_charge(self) -> None:
        panel = WorkflowPanel()
        panel.set_state("idle", total_charges=3)
        self.assertEqual(panel.current_charge_label.text(), "—")
        with self.assertRaises(ValueError):
            panel.set_state("unknown")
        with self.assertRaises(ValueError):
            panel.set_state("roasting", total_charges=-1)
        with self.assertRaises(ValueError):
            panel.set_state("roasting", current_charge=0)

    def test_compact_width_hides_only_secondary_workflow_copy(self) -> None:
        panel = WorkflowPanel()
        panel.resize(240, 220)
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel.property("compactMode"))
        self.assertFalse(panel.subtitle.isVisible())
        self.assertTrue(panel.status_badge.isVisible())
        self.assertEqual(panel.layout().contentsMargins().left(), 8)
        self.assertTrue(all(row.isVisible() for row in panel.state_rows.values()))

    def test_short_height_compact_mode_keeps_current_status_and_hides_detail_rows(self) -> None:
        panel = WorkflowPanel()
        panel.resize(272, 60)
        panel.show()
        panel.set_compact_mode(True)
        self.app.processEvents()

        self.assertTrue(panel.status_badge.isVisible())
        self.assertTrue(panel.property("compactMode"))
        self.assertTrue(all(not row.isVisible() for row in panel.state_rows.values()))
        self.assertTrue(all(not card.isVisible() for card in panel._counter_cards))
        self.assertEqual(panel.layout().contentsMargins().left(), 5)


class ArchiveSummaryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_summary_renders_archive_fields_and_partial_update(self) -> None:
        panel = ArchiveSummaryPanel()
        source = {
            "charge_number": 3,
            "batch_number": 17,
            "coffee_name": "日晒耶加",
            "bean_origin": "埃塞俄比亚",
            "charge_weight_g": 350,
            "duration_s": 612,
            "drop_time_s": 598,
            "drop_bt": 205.4,
            "fc_start_s": 482,
            "gas_action_count": 7,
            "damper_action_count": 4,
            "rpm_action_count": 2,
        }
        original = dict(source)

        panel.set_summary(source)

        self.assertEqual(source, original)
        self.assertEqual(panel.value_labels["identity"].text(), "#3 · Batch #0017")
        self.assertEqual(panel.value_labels["bean"].text(), "日晒耶加 · 埃塞俄比亚")
        self.assertEqual(panel.value_labels["weight"].text(), "350 g")
        self.assertEqual(panel.value_labels["duration"].text(), "10:12")
        self.assertEqual(panel.value_labels["drop"].text(), "09:58 · 205.4 °C")
        self.assertEqual(panel.value_labels["fc_start"].text(), "08:02")
        self.assertEqual(panel.value_labels["gas_actions"].text(), "7 次")
        self.assertEqual(panel.value_labels["damper_actions"].text(), "4 次")
        self.assertEqual(panel.value_labels["rpm_actions"].text(), "2 次")
        self.assertEqual(panel.saved_badge.text(), "已保存")

        panel.update({"duration_s": 620, "gas_action_count": 8})
        self.assertEqual(panel.value_labels["duration"].text(), "10:20")
        self.assertEqual(panel.value_labels["gas_actions"].text(), "8 次")
        self.assertEqual(panel.value_labels["bean"].text(), "日晒耶加 · 埃塞俄比亚")

        copy = panel.summary()
        copy["coffee_name"] = "已修改的副本"
        self.assertEqual(panel.summary()["coffee_name"], "日晒耶加")

    def test_empty_state_and_data_methods_have_no_io_side_effects(self) -> None:
        panel = ArchiveSummaryPanel()
        self.assertFalse(panel.summary_host.isVisible())
        self.assertEqual(panel.empty_label.text(), "暂无已封存锅次")

        with (
            mock.patch.object(sqlite3, "connect") as connect,
            mock.patch.object(pathlib.Path, "open") as path_open,
            mock.patch.object(pathlib.Path, "write_text") as write_text,
        ):
            panel.set_summary(batch_number="B-42", bean="哥伦比亚")
            panel.update(rpm_action_count=3)

        connect.assert_not_called()
        path_open.assert_not_called()
        write_text.assert_not_called()
        self.assertEqual(panel.value_labels["identity"].text(), "Batch #B-42")
        self.assertEqual(panel.value_labels["rpm_actions"].text(), "3 次")

    def test_native_qwidget_update_overloads_are_preserved(self) -> None:
        panel = ArchiveSummaryPanel()
        panel.update()
        panel.update(QtCore.QRect(0, 0, 10, 10))

    def test_compact_archive_keeps_values_and_status_visible(self) -> None:
        panel = ArchiveSummaryPanel()
        panel.set_summary(batch_number=3, duration_s=120)
        panel.resize(240, 360)
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel.property("compactMode"))
        self.assertFalse(panel.subtitle.isVisible())
        self.assertTrue(panel.saved_badge.isVisible())
        self.assertTrue(panel.summary_host.isVisible())
        self.assertEqual(panel.layout().contentsMargins().left(), 8)

    def test_short_height_archive_keeps_only_saved_state_header(self) -> None:
        panel = ArchiveSummaryPanel()
        panel.set_summary(batch_number=3, duration_s=120)
        panel.resize(286, 52)
        panel.show()
        panel.set_compact_mode(True)
        self.app.processEvents()

        self.assertTrue(panel.saved_badge.isVisible())
        self.assertFalse(panel.subtitle.isVisible())
        self.assertFalse(panel.summary_host.isVisible())
        self.assertEqual(panel.layout().contentsMargins().left(), 5)


if __name__ == "__main__":
    unittest.main()
