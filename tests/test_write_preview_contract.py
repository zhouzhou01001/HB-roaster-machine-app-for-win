from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.ui.main_window import MainWindow
from app.ui.preview_center import TAB_NAMES, PreviewCenterDialog


class FakeSerialManager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def connect(self, *_args, **_kwargs) -> None:
        pass

    def disconnect(self) -> None:
        pass


class CommitProxy:
    def __init__(self, connection) -> None:
        self._connection = connection
        self.commit = Mock()

    def __getattr__(self, name):
        return getattr(self._connection, name)


class WritePreviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        root = Path(self.workspace.name)
        self.serial_patch = patch("app.ui.main_window.SerialManager", return_value=FakeSerialManager())
        self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        self.window = MainWindow(root / "writes.hbroast", root / "channels.json")
        self.addCleanup(self.window.close)
        self.window._replace_session_buffer(
            samples=[{"time_s": 1.0, "it": 120.0, "et": 110.0, "bt": 100.0, "work": 90.0}],
            events=[{"event_type": "CHARGE", "time_s": 0.0, "bt": 95.0}],
            actions=[{"time_s": 0.0, "gas_mbar": 20.0, "gas_kpa": 2.0, "damper_level": 5.0, "rpm_level": 6.0}],
        )

    def _cancel(self) -> PreviewCenterDialog:
        dialog = self.window._preview_dialog
        self.assertIsNotNone(dialog)
        dialog.close_button.click()
        self.app.processEvents()
        return dialog

    def _confirm_twice(self) -> PreviewCenterDialog:
        dialog = self.window._preview_dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.confirm_button.isVisible())
        dialog.confirm_button.click()
        dialog.confirm_button.click()
        self.app.processEvents()
        return dialog

    def test_dialog_snapshot_selects_confirmation_tab_and_button_text(self) -> None:
        callback = Mock()
        snapshot = self.window._build_write_preview_snapshot(
            "保存批次",
            "确认写入测试",
            save_fields={"字段": "值"},
        )
        dialog = self.window._show_write_preview(snapshot, callback, "测试写入")
        self.app.processEvents()

        self.assertEqual(dialog.tabs.currentIndex(), TAB_NAMES.index("保存批次"))
        self.assertEqual(dialog.confirm_button.text(), "确认写入测试")
        dialog.tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.assertFalse(dialog.confirm_button.isVisible())
        callback.assert_not_called()
        dialog.tabs.setCurrentIndex(TAB_NAMES.index("保存批次"))
        self._confirm_twice()
        callback.assert_called_once_with()

    def test_export_files_write_nothing_before_confirmation_or_after_cancel(self) -> None:
        root = Path(self.workspace.name)
        csv_target = root / "curve.csv"
        json_target = root / "curve.json"
        file_choices = [(str(csv_target), ""), (str(json_target), "")]
        with (
            patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", side_effect=file_choices * 2),
        ):
            self.window._export_data()
            self.assertFalse(csv_target.exists())
            self.assertFalse(json_target.exists())
            self.assertFalse(csv_target.with_suffix(".report.txt").exists())
            cancelled_dialog = self._cancel()
            cancelled_dialog.confirm_button.click()
            self.app.processEvents()
            self.assertFalse(csv_target.exists())
            self.assertFalse(json_target.exists())
            self.assertFalse(csv_target.with_suffix(".report.txt").exists())

            self.window._export_data()
            self._confirm_twice()
            self.assertIn("work_ror", csv_target.read_text(encoding="utf-8"))
            self.assertIn("100.0", json_target.read_text(encoding="utf-8"))
            self.assertTrue(csv_target.with_suffix(".report.txt").is_file())

    def test_chart_pdf_and_profile_write_only_from_confirmed_preview(self) -> None:
        root = Path(self.workspace.name)
        pdf_target = root / "chart.pdf"
        self.window._roast_finished = True
        with (
            patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(pdf_target), "")),
            patch("app.ui.main_window.export_chart_pdf") as pdf_write,
        ):
            self.window._export_current_chart_pdf()
            pdf_write.assert_not_called()
            self._cancel()
            pdf_write.assert_not_called()
            self.window._export_current_chart_pdf()
            self._confirm_twice()
            pdf_write.assert_called_once()

        self.window._batch_id = 1
        with (
            patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("参考 A", True)),
            patch("PySide6.QtWidgets.QInputDialog.getMultiLineText", return_value=("备注", True)),
            patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")),
            patch("app.ui.main_window.save_profile_from_batch", return_value=7) as profile_write,
        ):
            self.window._save_current_as_profile()
            profile_write.assert_not_called()
            self._cancel()
            profile_write.assert_not_called()
            self.window._save_current_as_profile()
            self._confirm_twice()
            profile_write.assert_called_once_with(
                db=self.window.db,
                batch_id=1,
                name="参考 A",
                notes="备注",
            )

    def test_idle_batch_info_and_report_commit_are_confirmation_gated(self) -> None:
        self.window._batch_id = self.window.db.create_batch(coffee_name="旧名称")
        self.window.db.update_roast_info = Mock()
        payload = {
            "device": "HB",
            "operator": "tester",
            "coffee_name": "新名称",
            "green_weight": 350.0,
            "roasted_weight": 300.0,
        }
        with patch("app.ui.main_window.BatchInfoDialog") as dialog_type:
            dialog_type.return_value.exec.return_value = QtWidgets.QDialog.DialogCode.Accepted
            dialog_type.return_value.payload.return_value = payload
            self.window._edit_batch_info()
            self.window.db.update_roast_info.assert_not_called()
            self._cancel()
            self.window.db.update_roast_info.assert_not_called()
            self.window._edit_batch_info()
            self._confirm_twice()
            self.window.db.update_roast_info.assert_called_once()

        proxy = CommitProxy(self.window.db.conn)
        self.window.db.conn = proxy
        self.window._save_batch_report()
        proxy.commit.assert_not_called()
        self._cancel()
        proxy.commit.assert_not_called()
        self.window._save_batch_report()
        self._confirm_twice()
        proxy.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
