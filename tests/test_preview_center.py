import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from app.ui.preview_center import CSV_HEADERS, TAB_NAMES, PreviewCenterDialog


def sample_snapshot(mode="preview"):
    return {
        "mode": mode,
        "batch_number": 7,
        "end_roast": {
            "summary": {
                "锅次": "#7",
                "本锅时长": "10:00",
                "DROP 时刻": "09:58",
                "事件": "8 / 8",
                "采样点": "600 点",
                "曲线": "11 条",
                "预计落库": "~48.2 KB",
                "写入目标": "charges 表",
            },
            "bt_curve": [(0, 92.0), (300, 160.0), (600, 205.0)],
            "events": [
                {"event_type": "CHARGE", "time": "00:05", "bt": "92.0"},
                {"event_type": "DROP", "time": "09:58", "bt": "205.0"},
            ],
            "after": "确认后封存第 7 锅；关闭预览不会产生任何写入。",
        },
        "export_curve": {
            "summary": {"文件": "batch_0007.csv", "总行数": "600 行", "大小": "~42 KB", "列数": "8 列"},
            "rows": [
                {"time_s": 0, "ET_C": 180.0, "BT_C": 92.0, "IT_C": 160.0, "XT_C": 150.0, "GAS_mbar": 18.0, "DAM": 6, "SPD": 7},
                {"time_s": 1, "ET_C": 181.0, "BT_C": 92.5, "IT_C": 160.5, "XT_C": 150.5, "GAS_mbar": 18.0, "DAM": 6, "SPD": 7},
            ],
        },
        "save_batch": {
            "fields": {"批次号": "Batch #0007", "豆种·产地": "日晒耶加 · 埃塞俄比亚", "投豆量": "350 g"},
        },
        "replay": {
            "summary": {"回放对象": "锅次 #7", "时长": "10:00", "数据点": "600 点", "模式": "只读回放"},
            "duration_s": 600,
            "events": [{"event_type": "CHARGE", "time": "00:05", "time_s": 5}],
        },
        "compare": {
            "summary": {"对比": "#6 vs #7", "峰值 BT": "202→205°C", "差值": "+3.0°C", "叠加": "11 曲线"},
            "previous_curve": [(0, 90), (600, 202)],
            "current_curve": [(0, 92), (600, 205)],
        },
        "config_changes": {
            "rows": [{"item": "采样频率", "current": "1 Hz", "new": "2 Hz"}],
        },
    }


class PreviewCenterDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def make_dialog(self, snapshot):
        dialog = PreviewCenterDialog(snapshot)
        self.dialogs.append(dialog)
        return dialog

    def test_requires_a_dict_snapshot(self):
        with self.assertRaises(TypeError):
            PreviewCenterDialog(None)

    def test_builds_six_tabs_with_exact_documented_names(self):
        dialog = self.make_dialog(sample_snapshot())
        self.assertEqual(dialog.tabs.count(), 6)
        self.assertEqual(tuple(dialog.tabs.tabText(index) for index in range(dialog.tabs.count())), TAB_NAMES)
        self.assertEqual(dialog.windowTitle(), "统一只读预览")
        badge = dialog.findChild(QtWidgets.QLabel, "readOnlyBadge")
        self.assertIsNotNone(badge)
        self.assertIn("未写入任何文件", badge.text())
        self.assertEqual(badge.property("statusTone"), "success")
        self.assertEqual(dialog.findChild(QtWidgets.QFrame, "previewHeader").property("uiRole"), "dialogHeader")
        self.assertEqual(dialog.findChild(QtWidgets.QFrame, "previewFooter").property("uiRole"), "dialogActions")
        self.assertTrue(dialog.findChildren(QtWidgets.QFrame, "summaryCard"))
        self.assertFalse(
            any(label.text().startswith("原：") for label in dialog.findChildren(QtWidgets.QLabel))
        )

    def test_preview_mode_never_shows_confirmation_button(self):
        dialog = self.make_dialog(sample_snapshot("preview"))
        dialog.show()
        self.app.processEvents()
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertFalse(dialog.confirm_button.isVisible())
        self.assertEqual(dialog.close_button.text(), "关闭")
        self.assertIn("纯预览模式", dialog.footer_note.text())

    def test_confirmation_is_visible_only_on_end_roast_tab(self):
        dialog = self.make_dialog(sample_snapshot("confirm"))
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.confirm_button.isVisible())
        self.assertEqual(dialog.confirm_button.text(), "✓ 确认执行 · 结束烘焙并封存 #7")
        for index in range(1, dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertFalse(dialog.confirm_button.isVisible())
        dialog.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(dialog.confirm_button.isVisible())
        self.assertEqual(dialog.close_button.text(), "取消 / 关闭")

    def test_confirmation_only_emits_a_deep_copied_snapshot(self):
        source = sample_snapshot("confirm")
        dialog = self.make_dialog(source)
        emitted = []
        dialog.confirm_requested.connect(emitted.append)
        source["batch_number"] = 99
        source["end_roast"]["summary"]["锅次"] = "#99"

        dialog.show()
        self.app.processEvents()
        dialog.confirm_button.click()
        self.app.processEvents()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["batch_number"], 7)
        self.assertEqual(emitted[0]["end_roast"]["summary"]["锅次"], "#7")
        emitted[0]["batch_number"] = 123
        self.assertEqual(dialog.snapshot()["batch_number"], 7)
        self.assertTrue(dialog.isVisible(), "确认意图应由宿主处理，对话框不得自行执行或关闭")

    def test_construction_tab_switch_and_confirmation_do_not_open_files(self):
        with patch("builtins.open", side_effect=AssertionError("preview must not perform file IO")):
            dialog = self.make_dialog(sample_snapshot("confirm"))
            dialog.show()
            self.app.processEvents()
            for index in range(dialog.tabs.count()):
                dialog.tabs.setCurrentIndex(index)
                self.app.processEvents()
            dialog.tabs.setCurrentIndex(0)
            dialog.confirm_button.click()
            self.app.processEvents()

    def test_preview_tables_are_read_only_and_csv_columns_are_fixed(self):
        dialog = self.make_dialog(sample_snapshot())
        csv_table = dialog.findChild(QtWidgets.QTableWidget, "csvPreviewTable")
        event_table = dialog.findChild(QtWidgets.QTableWidget, "endRoastEventsTable")
        config_table = dialog.findChild(QtWidgets.QTableWidget, "configChangesTable")
        self.assertIsNotNone(csv_table)
        self.assertIsNotNone(event_table)
        self.assertIsNotNone(config_table)
        self.assertEqual(tuple(csv_table.horizontalHeaderItem(index).text() for index in range(csv_table.columnCount())), CSV_HEADERS)
        self.assertLessEqual(csv_table.rowCount(), 7)
        for table in (csv_table, event_table, config_table):
            self.assertEqual(table.editTriggers(), QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.assertFalse(table.showGrid())
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        self.assertFalse(bool(item.flags() & QtCore.Qt.ItemFlag.ItemIsEditable))

    def test_escape_and_both_close_buttons_reject_the_dialog(self):
        dialog = self.make_dialog(sample_snapshot())
        dialog.show()
        self.app.processEvents()
        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Rejected)

        second = self.make_dialog(sample_snapshot())
        second.show()
        self.app.processEvents()
        second.header_close_button.click()
        self.app.processEvents()
        self.assertFalse(second.isVisible())

        third = self.make_dialog(sample_snapshot("confirm"))
        third.show()
        self.app.processEvents()
        third.close_button.click()
        self.app.processEvents()
        self.assertFalse(third.isVisible())


if __name__ == "__main__":
    unittest.main()
