import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from app.roast.workflow import RoastFlowState, RoastStage
from app.ui.action_panel import ActionPanel
from app.ui.archive_summary_panel import ArchiveSummaryPanel
from app.ui.event_panel import EventPanel
from app.ui.main_window import MainWindow, THEME_TOKENS
from app.ui.workflow_panel import WorkflowPanel


class _FakeSerialManager(QtCore.QObject):
    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def disconnect(self) -> None:
        pass


class MainWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.serial_patch = patch("app.ui.main_window.SerialManager", return_value=_FakeSerialManager())
        self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        root = Path(self.workspace.name)
        self.window = MainWindow(root / "layout.hbroast", root / "channel.json")
        self.addCleanup(self.window.close)
        self.window.show()
        self.app.processEvents()

    def test_artisan_workbench_zones_and_six_lcd_cards(self) -> None:
        layout = self.window.centralWidget().layout()
        zone_names = [layout.itemAt(index).widget().objectName() for index in range(layout.count())]
        self.assertEqual(
            zone_names,
            ["topNav", "processStrip", "mainPanelSplitter", "bottomActionBar"],
        )
        self.assertEqual(len(self.window.visible_metric_cards), 6)
        self.assertTrue(all(card.isVisible() for card in self.window.visible_metric_cards))
        self.assertTrue(self.window.metric_it.sub_value_widget.isVisible())
        self.assertTrue(self.window.metric_exhaust.sub_value_widget.isVisible())
        self.assertFalse(self.window.top_nav.isVisible())
        self.assertFalse(self.window.process_strip.isVisible())
        self.assertTrue(self.window.menu_brand_action.isVisible())
        self.assertTrue(self.window.menu_status_action.isVisible())
        self.assertEqual(self.window.bottom_action_bar.height(), self.window._ui_value(52))
        self.assertEqual(self.window.event_card.height(), self.window._ui_value(82))
        self.assertGreaterEqual(self.window.chart_card.layout().indexOf(self.window.event_card), 0)

    def test_temperature_lcd_text_fits_at_narrow_supported_width(self) -> None:
        self.window.resize(1280, 720)
        temperature_cards = (
            self.window.metric_bt,
            self.window.metric_et,
            self.window.metric_it,
            self.window.metric_exhaust,
        )
        for card in temperature_cards:
            card.value_widget.setText("-123.4")
            card.sub_value_widget.setText("-123")
        self.app.processEvents()

        for card in temperature_cards:
            self.assertEqual(card.value_unit_widget.text(), "°C")
            self.assertEqual(card.sub_label_widget.text(), "RoR")
            self.assertEqual(card.sub_unit_widget.text(), "°C/min")
            widgets = (
                card.findChild(QtWidgets.QLabel, "metricLabel"),
                card.value_widget,
                card.value_unit_widget,
                card.sub_label_widget,
                card.sub_value_widget,
                card.sub_unit_widget,
            )
            for widget in widgets:
                self.assertTrue(widget.isVisible())
                text_width = widget.fontMetrics().horizontalAdvance(widget.text())
                self.assertGreaterEqual(widget.contentsRect().width(), text_width)

    def test_documented_theme_tokens_and_top_navigation_copy(self) -> None:
        menu_actions = self.window.menuBar().actions()
        help_index = next(
            index for index, action in enumerate(menu_actions) if action.text() == "帮助"
        )
        self.assertIs(menu_actions[help_index + 1], self.window.menu_brand_action)
        self.assertIs(menu_actions[help_index + 2], self.window.menu_status_action)
        self.assertEqual(self.window.menu_brand_action.text(), "周周 ROAST · 烘焙曲线")
        self.assertIn("尚未采集", self.window.menu_status_action.text())
        self.assertEqual(
            {key: THEME_TOKENS["dark"][key] for key in ("bg", "panel", "panel2", "line", "txt", "dim", "accent")},
            {
                "bg": "#14171c",
                "panel": "#1c212a",
                "panel2": "#242b38",
                "line": "#2c3442",
                "txt": "#e8ecf2",
                "dim": "#8a94a6",
                "accent": "#f0821e",
            },
        )
        self.assertEqual(
            {key: THEME_TOKENS["light"][key] for key in ("bg", "panel", "panel2", "line", "txt", "dim", "accent")},
            {
                "bg": "#f3f5f8",
                "panel": "#ffffff",
                "panel2": "#eef1f6",
                "line": "#d8dee8",
                "txt": "#20242c",
                "dim": "#66707f",
                "accent": "#f0821e",
            },
        )
        self.assertEqual(self.window.brand_label.accessibleName(), "周周 ROAST")
        self.assertIn("#f0821e", self.window.brand_label.text())
        self.assertEqual(
            tuple(self.window.module_nav_buttons),
            ("烘焙曲线", "采集", "对比", "回放", "配置", "档案"),
        )
        self.assertIs(self.window.module_nav_buttons["采集"].defaultAction(), self.window.act_connect)
        self.assertIs(self.window.module_nav_buttons["对比"].defaultAction(), self.window.act_compare)
        self.assertIs(self.window.module_nav_buttons["回放"].defaultAction(), self.window.act_replay)
        self.assertIs(self.window.module_nav_buttons["配置"].defaultAction(), self.window.act_display)
        self.assertIs(self.window.module_nav_buttons["档案"].defaultAction(), self.window.act_open)
        self.assertEqual(self.window.power_status_label.text(), "● 供电正常")
        self.assertEqual(self.window.preview_center_button.text(), "预览中心")
        self.assertEqual(self.window.theme_button.text(), "切换主题")
        self.assertIs(self.window.theme_button.defaultAction(), self.window.act_theme)
        self.assertFalse(any("原：" in label.text() for label in self.window.findChildren(QtWidgets.QLabel)))

    def test_preview_center_entry_is_pure_preview_and_chart_header_uses_batch_metadata(self) -> None:
        self.window.workflow.batch_number = 7
        self.window._batch_metadata = {"coffee_name": "日晒耶加", "green_weight": 350.0}
        self.window._update_chart_header()
        self.assertEqual(self.window.chart_title_label.text(), "烘焙曲线")
        self.assertEqual(
            self.window.chart_subtitle_label.text(),
            "Batch #0007 · 日晒耶加 · 350g",
        )
        self.assertIn("曲线显示", self.window.chart_subtitle_label.toolTip())

        self.window.preview_center_button.click()
        self.app.processEvents()
        self.assertIsNotNone(self.window._preview_dialog)
        self.assertFalse(self.window._preview_dialog.confirm_button.isVisible())
        self.window._preview_dialog.close()

    def test_right_rail_contains_four_panels_in_required_order(self) -> None:
        splitter = self.window.right_panel_splitter
        self.assertEqual(splitter.count(), 3)
        self.assertIsInstance(splitter.widget(0).findChild(ActionPanel), ActionPanel)
        self.assertIs(splitter.widget(1), self.window.workflow_panel)
        self.assertIsInstance(splitter.widget(2), ArchiveSummaryPanel)
        self.assertIs(self.window.lcd_band.parentWidget(), self.window.chart_card)

    def test_legacy_toolbar_is_available_but_hidden_by_default(self) -> None:
        self.assertFalse(self.window.main_toolbar.isVisible())
        self.assertEqual(self.window.act_toggle_legacy_toolbar.text(), "显示传统工具栏")

    def test_short_height_keeps_primary_right_rail_actions_visible(self) -> None:
        self.window.resize(1280, 720)
        self.app.processEvents()

        self.assertFalse(self.window.workflow_panel.status_badge.isVisible())
        self.assertTrue(self.window.action_panel.add_btn.isVisible())
        self.assertFalse(self.window.action_panel.note_edit.isVisible())
        self.assertTrue(all(card._slider.isVisible() for card in self.window.action_panel._control_cards))
        self.assertTrue(all(card.layout().indexOf(card._slider) == 2 for card in self.window.action_panel._control_cards))
        self.assertTrue(all(card._slider.minimumWidth() >= 180 for card in self.window.action_panel._control_cards))

    def test_responsive_widths_keep_horizontal_workspace_without_main_scroll(self) -> None:
        self.window.resize(1280, 720)
        self.app.processEvents()
        self.assertEqual(self.window.right_workspace.width(), self.window._ui_value(286))
        self.assertEqual(self.window.lcd_metrics_layout.getItemPosition(5)[1], 0)

        self.window.resize(1180, 720)
        self.app.processEvents()
        self.assertEqual(self.window.width(), 1180)
        self.assertEqual(self.window.right_workspace.width(), self.window._ui_value(260))
        self.assertFalse(self.window.event_card.subtitle_label.isVisible())
        self.assertFalse(self.window.chart_subtitle_label.isVisible())

        self.window.resize(980, 720)
        self.app.processEvents()
        self.assertEqual(self.window.width(), 980)
        self.assertEqual(self.window.workspace_splitter.orientation(), QtCore.Qt.Orientation.Horizontal)
        self.assertEqual(self.window.right_workspace.width(), self.window._ui_value(260))
        positions = [
            self.window.lcd_metrics_layout.getItemPosition(index)
            for index in range(self.window.lcd_metrics_layout.count())
        ]
        self.assertEqual({row for row, _span, _column, _column_span in positions}, set(range(6)))
        self.assertTrue(all(column == 0 for _row, column, _rows, _columns in positions))
        self.assertFalse(self.window.bottom_status_label.isVisible())
        scroll_areas = self.window.centralWidget().findChildren(QtWidgets.QScrollArea)
        self.assertEqual(scroll_areas, [])

    def test_workflow_display_sync_has_no_state_side_effect(self) -> None:
        self.window.workflow.stage = RoastStage.BETWEEN
        self.window.workflow.state = RoastFlowState.CONNECTED
        self.window.workflow.batch_number = 2
        self.window._completed_charges = 2
        self.window._has_valid_device_sample = True
        self.window._sync_workflow_ui()

        self.assertEqual(self.window.workflow.stage, RoastStage.BETWEEN)
        self.assertEqual(self.window.workflow_panel.current_state, "between")
        self.assertEqual(self.window.workflow_panel.total_charge_label.text(), "2 锅")
        self.assertEqual(self.window.process_batch_label.text(), "第 2 锅")
        self.assertTrue(self.window.start_next_batch_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
