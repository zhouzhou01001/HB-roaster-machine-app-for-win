from __future__ import annotations

import math
import time
from collections import deque
from pathlib import Path
from typing import Callable, List

from PySide6 import QtCore, QtGui, QtWidgets

from app.database.database import RoastDatabase
from app.debug.session_debug_logger import SessionDebugLogger
from app.device.channel_mapping import ChannelMapper
from app.device.serial_manager import SerialManager
from app.device.hb_parser import HbParser
from app.export.csv_export import SAMPLE_FIELDS, export_samples
from app.export import export_atomic
from app.export.json_export import export_roast_json
from app.export.report import build_report
from app.export.chart_pdf import export_chart_pdf
from app.roast.events import add_event
from app.roast.reference_profile import save_profile_from_batch, list_reference_profiles, load_reference_profile, export_profile_to_json
from app.roast.compare import detect_tp, development_metrics
from app.roast.milestone_forecast import predict_milestone_times
from app.roast.analysis import analyze_roast
from app.roast.importer import import_csv_batch, restore_imported_sample
from app.roast.projection import predict_ror
from app.roast.ror import calculate_ror
from app.roast.sample_quality import SampleQualityGate
from app.roast.configuration import RoastConfiguration, RoastConfigurationState, Theme
from app.roast.preview import build_end_roast_preview, build_export_preview
from app.roast.workflow import RoastSessionBuffer, RoastStage, RoastWorkflow, WorkflowError
from app.ui.action_panel import ActionPanel
from app.ui.curve_widget import CurveWidget
from app.ui.event_panel import EventPanel
from app.ui.batch_info_dialog import BatchInfoDialog
from app.ui.compare_window import CompareWindow
from app.ui.replay_window import ReplayWindow
from app.ui.target_panel import TargetPanel
from app.ui.display_settings_dialog import DisplaySettingsDialog
from app.ui.axis_settings_dialog import AxisSettingsDialog
from app.ui.preview_center import PreviewCenterDialog
from app.ui.workflow_panel import WorkflowPanel
from app.ui.archive_summary_panel import ArchiveSummaryPanel
from app.utils.paths import (
    default_debug_log_dir,
    default_debug_log_path,
)
from app.version import APP_VERSION


THEME_TOKENS = {
    "dark": {
        "bg": "#14171c", "panel": "#1c212a", "panel2": "#242b38",
        "line": "#2c3442", "line_soft": "#2c3442", "txt": "#e8ecf2",
        "dim": "#8a94a6", "faint": "#8a94a6", "accent": "#f0821e",
        "accent_dim": "#f0821e", "accent_soft": "rgba(240,130,30,0.14)",
        "btn": "#242b38", "btn_hover": "#2c3442", "ok": "#35c98e",
        "danger": "#e8544f", "lcd_bg": "#1c212a", "lcd_top": "#242b38",
        "lcd_on": "#f0821e", "lcd_label": "#8a94a6", "lcd_sub": "#8a94a6",
        "track": "#2c3442", "accent_text": "#ffffff", "lcd_border": "#2c3442",
        "danger_text": "#ffffff", "ok_text": "#14171c", "et": "#e8544f",
        "bt": "#ff8c2f", "ror": "#35c98e", "inlet": "#4fb3ff", "exhaust": "#b06bff",
    },
    "light": {
        "bg": "#f3f5f8", "panel": "#ffffff", "panel2": "#eef1f6",
        "line": "#d8dee8", "line_soft": "#d8dee8", "txt": "#20242c",
        "dim": "#66707f", "faint": "#66707f", "accent": "#f0821e",
        "accent_dim": "#f0821e", "accent_soft": "rgba(240,130,30,0.12)",
        "btn": "#eef1f6", "btn_hover": "#d8dee8", "ok": "#35c98e",
        "danger": "#e8544f", "lcd_bg": "#ffffff", "lcd_top": "#eef1f6",
        "lcd_on": "#f0821e", "lcd_label": "#66707f", "lcd_sub": "#66707f",
        "track": "#d8dee8", "accent_text": "#ffffff", "lcd_border": "#d8dee8",
        "danger_text": "#ffffff", "ok_text": "#ffffff", "et": "#e8544f",
        "bt": "#ff8c2f", "ror": "#35c98e", "inlet": "#4fb3ff", "exhaust": "#b06bff",
    },
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        db_path: Path = Path("data/default.hbroast"),
        channel_mapping_path: Path = Path("config/channel.json"),
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"烘焙软件 v{APP_VERSION}")
        self._ui_scale = self._resolve_ui_scale_factor()
        self.resize(self._ui_value(1280), self._ui_value(720))

        self._default_open_dir = Path(db_path).expanduser().resolve().parent
        self.db = RoastDatabase(db_path)
        self.mapper = ChannelMapper(channel_mapping_path)
        self.parser = HbParser()
        self.manager = SerialManager(parser=self.parser)
        self.workflow = RoastWorkflow()
        self.session_buffer = RoastSessionBuffer()
        self.samples: List[dict] = self.session_buffer.samples
        self.events = self.session_buffer.events
        self.actions = self.session_buffer.actions
        self._stage_log: list[dict] = []
        self._batch_metadata: dict = {}
        self._pending_archive_snapshot: dict | None = None
        self._preview_dialog: PreviewCenterDialog | None = None
        self._last_archive_result = None
        self._completed_charges = 0
        self._debug_logger: SessionDebugLogger | None = None
        self._debug_log_path = None
        self.ror_window_seconds = 10
        self._settings = QtCore.QSettings("HB", "RoastMonitor")
        configured_theme = Theme.LIGHT if self._settings.value("theme", "深色工作台") == "浅色工作台" else Theme.DARK
        self.configuration_state = RoastConfigurationState(
            RoastConfiguration(
                ror_window_seconds=self.ror_window_seconds,
                theme=configured_theme,
                auto_tp_enabled=self._setting_bool(self._settings.value("auto_tp_enabled", False), False),
            )
        )
        self.font_size = int(self._settings.value("font_size", 12))
        self.curve_scale = float(self._settings.value("curve_scale", 1.0))
        self.yellow_target_c = float(self._settings.value("yellow_target_c", 150.0))
        self.fc_target_c = float(self._settings.value("fc_target_c", 195.0))
        self.background_profile_name = ""
        self.session_active = False
        self.selected_source = ""
        self.selected_baudrate = 115200
        self.selected_profile = "HB_MODEL_S"
        self.selected_source_display = ""
        self.selected_source_meta: dict[str, object] = {}
        self.quality_gate = SampleQualityGate()
        self._live_samples = deque()
        self._last_live_sample = None
        self._capture_offset_s = 0.0
        self._last_capture_time_s = None
        self._last_capture_monotonic = None
        self._rebase_capture_on_next_sample = False
        self._batch_id = None
        self._history_source = "legacy"
        self._batch_prepared_for_monitoring = False
        self._roast_finished = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_timer = QtCore.QTimer(self)
        self._reconnect_timer.setInterval(1200)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

        self._build_toolbar()
        self._build_menu_bar()
        self._build_ui()
        self._restore_axis_settings()
        self._apply_theme()
        self._bind()
        self._configure_three_stage_flow()
        self._start_batch()

    def _resolve_ui_scale_factor(self) -> float:
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return 1.0
        short_edge = min(screen.availableGeometry().width(), screen.availableGeometry().height())
        if short_edge >= 2160:
            return 1.5
        if short_edge >= 1600:
            return 1.3
        if short_edge >= 1440:
            return 1.15
        if short_edge >= 1080:
            return 1.05
        return 1.0

    def _ui_value(self, value: int) -> int:
        return max(1, int(round(float(value) * self._ui_scale)))

    def _apply_theme(self, theme_name: str | None = None) -> None:
        settings = QtCore.QSettings("HB", "RoastMonitor")
        self.theme_name = theme_name or settings.value("theme", "深色工作台")
        dark = self.theme_name == "深色工作台"
        tokens = THEME_TOKENS["dark" if dark else "light"]
        button_min_height = self._ui_value(26)
        event_button_height = self._ui_value(46)
        compact_event_button_height = self._ui_value(34)
        font_size = self._ui_value(self.font_size)
        self.setStyleSheet(f"""
            QMainWindow {{ background: {tokens['bg']}; color: {tokens['txt']}; font-family: "Noto Sans SC", "Microsoft YaHei UI"; font-size: {font_size}px; }}
            QMenuBar#mainMenuBar {{ background: {tokens['panel']}; color: {tokens['dim']}; border-bottom: 1px solid {tokens['line']}; padding: 2px 12px; }}
            QMenuBar#mainMenuBar::item {{ padding: 4px 9px; border-radius: 4px; }}
            QMenuBar#mainMenuBar::item:selected {{ background: {tokens['panel2']}; color: {tokens['txt']}; }}
            QMenu {{ background: {tokens['panel']}; color: {tokens['txt']}; border: 1px solid {tokens['line']}; border-radius: 6px; padding: 4px; }}
            QMenu::item {{ padding: 6px 24px 6px 10px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {tokens['accent_soft']}; color: {tokens['lcd_on']}; }}
            QToolBar {{ background: {tokens['panel']}; border: 0; border-bottom: 1px solid {tokens['line']}; spacing: 4px; padding: 4px 10px; min-height: {self._ui_value(32)}px; }}
            QToolBar::separator {{ width: 1px; margin: 4px; background: {tokens['line']}; }}
            QSplitter::handle {{ background: {tokens['line']}; }}
            QSplitter::handle:hover {{ background: {tokens['accent']}; }}
            QToolButton, QPushButton {{ color: {tokens['txt']}; background: {tokens['btn']}; border: 1px solid {tokens['line']}; border-radius: 6px; padding: 5px 10px; min-height: {button_min_height}px; font-weight: 600; }}
            QToolButton:hover, QPushButton:hover {{ background: {tokens['btn_hover']}; border-color: {tokens['accent']}; }}
            QToolButton:pressed, QPushButton:pressed {{ background: {tokens['accent_dim']}; }}
            QToolButton:disabled, QPushButton:disabled {{ opacity: .45; color: {tokens['faint']}; }}
            QToolButton:focus, QPushButton:focus, QComboBox:focus, QAbstractSpinBox:focus, QCheckBox:focus {{ border: 2px solid {tokens['accent']}; }}
            QFrame#topNav, QFrame#sideCard, QFrame#chartCard, QFrame#controlCard, QFrame#eventCard, QFrame#eventDeck, QFrame#rightCard, QFrame#rightRailPanel, QFrame#workflowPanel, QFrame#archiveSummaryPanel {{ background: {tokens['panel']}; border: 1px solid {tokens['line']}; border-radius: 8px; }}
            QFrame#topNav {{ border-radius: 3px; }}
            QFrame#eventDeck {{ border-radius: 5px; }}
            QFrame#processStrip, QFrame#bottomActionBar {{ background: {tokens['panel']}; border: 1px solid {tokens['line']}; border-radius: 7px; }}
            QFrame#curveLegendBand, QFrame#targetDrawer {{ background: {tokens['panel2']}; border: 1px solid {tokens['line_soft']}; border-radius: 6px; }}
            QLabel#processBatchLabel {{ color: {tokens['lcd_on']}; font-family: Consolas, monospace; font-size: {self._ui_value(16)}px; font-weight: 700; min-width: {self._ui_value(52)}px; }}
            QLabel#processStageBadge, QLabel#workflowStatusBadge, QLabel#archiveStatusBadge {{ color: {tokens['dim']}; border: 1px solid {tokens['line']}; border-radius: 9px; padding: 2px 7px; font-weight: 600; }}
            QLabel#processStageBadge[statusTone="active"], QLabel#workflowStatusBadge[statusTone="active"] {{ color: {tokens['lcd_on']}; border-color: {tokens['accent']}; }}
            QLabel#processStageBadge[statusTone="warning"], QLabel#workflowStatusBadge[statusTone="warning"] {{ color: {tokens['danger']}; border-color: {tokens['danger']}; }}
            QLabel#processStageBadge[statusTone="success"], QLabel#workflowStatusBadge[statusTone="success"], QLabel#archiveStatusBadge[statusTone="success"] {{ color: {tokens['ok']}; border-color: {tokens['ok']}; }}
            QFrame#workflowStateRow {{ background: transparent; border: 0; border-left: 2px solid transparent; border-radius: 3px; }}
            QFrame#workflowStateRow[active="true"] {{ background: {tokens['panel2']}; border-left-color: {tokens['accent']}; }}
            QFrame#summaryCard, QFrame#actionControlCard {{ background: {tokens['panel2']}; border: 1px solid {tokens['line_soft']}; border-radius: 5px; }}
            QLabel[uiRole="secondaryText"], QLabel#rightPanelSubtitle, QLabel#bottomStatusLabel, QLabel#processStatsLabel {{ color: {tokens['dim']}; }}
            QLabel[uiRole="fieldLabel"] {{ color: {tokens['dim']}; font-size: {self._ui_value(10)}px; }}
            QLabel[uiRole="numericSummary"] {{ color: {tokens['txt']}; font-family: Consolas, monospace; font-weight: 700; }}
            QScrollArea#archiveSummaryScroll {{ background: transparent; border: 0; }}
            QFrame#chartControlBar {{ background: {tokens['panel2']}; border: 1px solid {tokens['line']}; border-radius: 6px; }}
            QFrame#lcdBand {{ background: {tokens['panel']}; border: 1px solid {tokens['line']}; border-radius: 8px; }}
            QFrame#metricCard {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {tokens['lcd_top']}, stop:1 {tokens['lcd_bg']}); border: 1px solid {tokens['lcd_border']}; border-radius: 6px; min-height: {self._ui_value(62)}px; }}
            QLabel#metricLabel {{ color: {tokens['lcd_label']}; font-size: {self._ui_value(13)}px; letter-spacing: 0.5px; }}
            QLabel#metricValue {{ color: {tokens['lcd_on']}; font-family: "Cascadia Code", Consolas, "Courier New", monospace; font-size: {self._ui_value(14)}px; font-weight: 700; }}
            QLabel#metricSubValue {{ color: {tokens['ror']}; font-family: "Cascadia Code", Consolas, "Courier New", monospace; font-size: {self._ui_value(12)}px; font-weight: 700; }}
            QLabel#metricUnit {{ color: {tokens['lcd_sub']}; font-family: "Cascadia Code", Consolas, "Courier New", monospace; font-size: {self._ui_value(10)}px; font-weight: 600; }}
            QLabel#metricSubLabel {{ color: {tokens['lcd_sub']}; font-size: {self._ui_value(11)}px; font-weight: 600; }}
            QFrame#metricCard[accentTone="et"] QLabel#metricValue {{ color: {tokens['et']}; }}
            QFrame#metricCard[accentTone="bt"] QLabel#metricValue {{ color: {tokens['bt']}; }}
            QFrame#metricCard[accentTone="ror"] QLabel#metricValue {{ color: {tokens['ror']}; }}
            QFrame#metricCard[accentTone="it"] QLabel#metricValue {{ color: {tokens['inlet']}; }}
            QFrame#metricCard[accentTone="exhaust"] QLabel#metricValue {{ color: {tokens['exhaust']}; }}
            QFrame#metricCard[accentTone="ok"] QLabel#metricValue {{ color: {tokens['ok']}; }}
            QLabel#appTitle {{ color: {tokens['txt']}; font-size: {self._ui_value(15)}px; font-weight: 700; }}
            QLabel#chartTitleLabel {{ color: {tokens['txt']}; font-size: {self._ui_value(12)}px; font-weight: 700; }}
            QLabel#chartSubtitleLabel {{ color: {tokens['dim']}; font-size: {self._ui_value(10)}px; }}
            QLabel#sectionTitle {{ color: {tokens['accent']}; font-size: {self._ui_value(12)}px; font-weight: 600; letter-spacing: 1px; }}
            QLabel#sourceBadge {{ color: {tokens['faint']}; background: {tokens['panel2']}; border: 1px solid {tokens['line_soft']}; border-radius: 4px; padding: 2px 5px; font-size: {self._ui_value(10)}px; }}
            QLabel#hintLabel, QLabel#statusLabel {{ color: {tokens['dim']}; font-size: {self._ui_value(11)}px; }}
            QLabel#powerStatusLabel {{ font-size: {self._ui_value(11)}px; font-weight: 600; }}
            QLabel#powerStatusLabel[powerState="normal"] {{ color: {tokens['ok']}; }}
            QLabel#powerStatusLabel[powerState="error"] {{ color: {tokens['danger']}; }}
            QLabel#manualOnlyLabel {{ color: {tokens['dim']}; background: {tokens['accent_soft']}; border: 1px dashed {tokens['accent_dim']}; border-radius: 6px; padding: 6px; }}
            QLabel#forecastLabel {{ color: {tokens['txt']}; padding: 3px 0; font-weight: 600; }}
            QPushButton#forecastChip {{ font-size: {self._ui_value(11)}px; border-radius: 11px; padding: 3px 10px; min-height: {self._ui_value(24)}px; background: {tokens['panel2']}; border: 1px solid {tokens['line_soft']}; color: {tokens['txt']}; }}
            QPushButton#forecastChip:disabled {{ color: {tokens['dim']}; border-color: {tokens['line']}; background: {tokens['panel']}; }}
            QLineEdit, QComboBox, QAbstractSpinBox {{ background: {tokens['panel2']}; color: {tokens['txt']}; border: 1px solid {tokens['line']}; border-radius: 6px; padding: 5px; min-height: {self._ui_value(22)}px; }}
            QCheckBox {{ color: {tokens['dim']}; spacing: 6px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {tokens['line']}; border-radius: 3px; background: {tokens['panel2']}; }}
            QCheckBox::indicator:checked {{ background: {tokens['accent']}; border-color: {tokens['accent']}; }}
            QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {tokens['track']}; }}
            QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border: 2px solid {tokens['panel']}; border-radius: 8px; background: {tokens['accent']}; }}
            QPushButton#eventButton, QPushButton[eventState="pending"] {{ min-height: {event_button_height}px; padding: 3px 3px; font-size: {self._ui_value(11)}px; border-style: dashed; color: {tokens['dim']}; }}
            QPushButton#eventButton:checked, QPushButton#eventButton[recordState="recorded"], QPushButton#eventButton[eventState="recorded"], QPushButton#eventButton[eventState="locked"] {{ background: {tokens['accent_soft']}; border-color: {tokens['accent']}; border-style: solid; color: {tokens['lcd_on']}; }}
            QPushButton#dropButton[recordState="locked"], QPushButton#dropButton[eventState="locked"] {{ background: {tokens['danger']}; color: {tokens['danger_text']}; border-color: {tokens['danger']}; min-height: {event_button_height}px; }}
            QWidget#eventPanel[compactMode="true"] QPushButton#eventButton, QWidget#eventPanel[compactMode="true"] QPushButton#dropButton {{ min-height: {compact_event_button_height}px; padding: 3px 2px; font-size: {self._ui_value(10)}px; }}
            QPushButton#startMonitorButton {{ background: {tokens['accent']}; color: {tokens['accent_text']}; border-color: {tokens['accent']}; }}
            QPushButton#lifecycleOnButton {{ background: {tokens['ok']}; color: {tokens['ok_text']}; border-color: {tokens['ok']}; }}
            QToolButton#navButton {{ background: transparent; color: {tokens['dim']}; border: 0; padding: 4px 9px; }}
            QToolButton#navButton:hover {{ background: {tokens['panel2']}; color: {tokens['txt']}; }}
            QToolButton#navButton:checked {{ background: {tokens['panel2']}; color: {tokens['txt']}; border: 1px solid {tokens['line']}; }}
            QListWidget {{ background: {tokens['panel2']}; border: 1px solid {tokens['line']}; border-radius: 6px; padding: 4px; }}
            QListWidget::item {{ padding: 7px 5px; border-bottom: 1px solid {tokens['line_soft']}; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {tokens['btn']}; min-height: 24px; border-radius: 5px; }}
            QStatusBar {{ background: {tokens['panel']}; color: {tokens['dim']}; border-top: 1px solid {tokens['line']}; }}
            QDialog {{ background: {tokens['panel']}; color: {tokens['txt']}; font-family: "Noto Sans SC", "Microsoft YaHei UI"; }}
        """)
        self.curve.set_theme(dark)
        self._apply_view_settings(save=False)
        settings.setValue("theme", self.theme_name)

    def _choose_theme(self) -> None:
        theme, ok = QtWidgets.QInputDialog.getItem(
            self, "切换界面主题", "主题", ["深色工作台", "浅色工作台"],
            0 if self.theme_name == "深色工作台" else 1, False,
        )
        if ok:
            self._apply_theme(theme)

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setObjectName("mainMenuBar")
        menu_bar.setNativeMenuBar(False)
        file_menu = menu_bar.addMenu("文件")
        file_menu.addActions((self.act_new, self.act_open, self.act_import, self.act_save, self.act_export, self.act_export_pdf))
        device_menu = menu_bar.addMenu("设备")
        device_menu.addActions((self.act_connect, self.act_start, self.act_stop, self.act_channels))
        view_menu = menu_bar.addMenu("视图")
        view_menu.addActions((self.act_background, self.act_clear_background, self.act_display, self.act_theme))
        view_menu.addSeparator()
        self.act_toggle_legacy_toolbar = self.main_toolbar.toggleViewAction()
        self.act_toggle_legacy_toolbar.setText("显示传统工具栏")
        view_menu.addAction(self.act_toggle_legacy_toolbar)
        tools_menu = menu_bar.addMenu("工具")
        tools_menu.addActions((self.act_replay, self.act_profile, self.act_compare, self.act_tp, self.act_set_tp, self.act_analyze))
        help_menu = menu_bar.addMenu("帮助")
        help_menu.addActions((self.act_open_debug_logs, self.act_export_debug_bundle))

        self.menu_brand_action = QtGui.QAction("周周 ROAST · 烘焙曲线", self)
        self.menu_brand_action.setObjectName("menuBrandAction")
        self.menu_brand_action.setEnabled(False)
        menu_bar.addAction(self.menu_brand_action)

        self.menu_status_action = QtGui.QAction(
            "待采集 · 采集总时长 00:00 · 已完成 0 锅",
            self,
        )
        self.menu_status_action.setObjectName("menuStatusAction")
        self.menu_status_action.setEnabled(False)
        menu_bar.addAction(self.menu_status_action)

    def _build_top_nav(self) -> QtWidgets.QFrame:
        nav = QtWidgets.QFrame()
        nav.setObjectName("topNav")
        layout = QtWidgets.QHBoxLayout(nav)
        layout.setContentsMargins(self._ui_value(10), self._ui_value(3), self._ui_value(10), self._ui_value(3))
        layout.setSpacing(self._ui_value(5))
        self.brand_label = QtWidgets.QLabel('周周 <span style="color:#f0821e">ROAST</span>')
        self.brand_label.setObjectName("appTitle")
        self.brand_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.brand_label.setAccessibleName("周周 ROAST")
        layout.addWidget(self.brand_label)
        module_actions = (
            ("烘焙曲线", None),
            ("采集", self.act_connect),
            ("对比", self.act_compare),
            ("回放", self.act_replay),
            ("配置", self.act_display),
            ("档案", self.act_open),
        )
        self.module_nav_buttons = {}
        for index, (label, action) in enumerate(module_actions):
            button = self._nav_button(label, action, checked=index == 0)
            self.module_nav_buttons[label] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.power_status_label = QtWidgets.QLabel("● 供电正常")
        self.power_status_label.setObjectName("powerStatusLabel")
        self.power_status_label.setProperty("powerState", "normal")
        layout.addWidget(self.power_status_label)
        self.preview_center_button = QtWidgets.QToolButton()
        self.preview_center_button.setObjectName("navButton")
        self.preview_center_button.setText("预览中心")
        self.preview_center_button.clicked.connect(self._open_preview_center)
        layout.addWidget(self.preview_center_button)
        self.theme_button = QtWidgets.QToolButton()
        self.theme_button.setObjectName("navButton")
        self.theme_button.setDefaultAction(self.act_theme)
        self.theme_button.setText("切换主题")
        self.theme_button.setToolTip("切换深色或浅色主题")
        layout.addWidget(self.theme_button)
        self.session_status_label = QtWidgets.QLabel(nav)
        self.session_status_label.setObjectName("statusLabel")
        self.session_status_label.hide()
        return nav

    def _nav_button(self, text: str, action: QtGui.QAction | None, checked: bool = False) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setObjectName("navButton")
        button.setText(text)
        button.setCheckable(checked)
        button.setChecked(checked)
        if action is not None:
            button.setDefaultAction(action)
            button.setText(text)
        return button

    def _panel_header(self, title: str, subtitle: str) -> QtWidgets.QWidget:
        header = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, self._ui_value(7))
        layout.setSpacing(self._ui_value(6))
        self.chart_title_label = QtWidgets.QLabel(title)
        self.chart_title_label.setObjectName("chartTitleLabel")
        layout.addWidget(self.chart_title_label)
        self.chart_subtitle_label = QtWidgets.QLabel(subtitle)
        self.chart_subtitle_label.setObjectName("chartSubtitleLabel")
        self.chart_subtitle_label.setProperty("uiRole", "secondaryText")
        layout.addWidget(self.chart_subtitle_label)
        layout.addStretch(1)
        return header

    def _update_chart_header(self) -> None:
        if not hasattr(self, "chart_subtitle_label"):
            return
        batch_value = self.workflow.batch_number or self._batch_id or 0
        try:
            batch_text = f"{int(batch_value):04d}"
        except (TypeError, ValueError):
            batch_text = str(batch_value)
        coffee_name = str(self._batch_metadata.get("coffee_name") or "未设置豆种")
        weight_value = self._batch_metadata.get(
            "charge_weight_g",
            self._batch_metadata.get("green_weight"),
        )
        try:
            weight = float(weight_value or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        weight_text = f"{weight:g}g" if weight > 0 else "--g"
        self.chart_title_label.setText("烘焙曲线")
        self.chart_subtitle_label.setText(
            f"Batch #{batch_text} · {coffee_name} · {weight_text}"
        )
        self.chart_subtitle_label.setToolTip("通过“曲线显示”菜单选择温度、RoR 和工艺曲线")

    def _set_power_status(self, normal: bool) -> None:
        if not hasattr(self, "power_status_label"):
            return
        self.power_status_label.setText("● 供电正常" if normal else "● 供电异常")
        self.power_status_label.setProperty("powerState", "normal" if normal else "error")
        self.power_status_label.style().unpolish(self.power_status_label)
        self.power_status_label.style().polish(self.power_status_label)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("工作台")
        self.main_toolbar = tb
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.setIconSize(QtCore.QSize(self._ui_value(20), self._ui_value(20)))
        style = self.style()
        self.act_new = QtGui.QAction("新建批次", self)
        self.act_new.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.act_open = QtGui.QAction("打开烘焙档案", self)
        self.act_open.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton))
        self.act_import = QtGui.QAction("导入 CSV", self)
        self.act_import.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowUp))
        self.act_save = QtGui.QAction("保存", self)
        self.act_save.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton))
        self.act_export = QtGui.QAction("导出", self)
        self.act_export.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton))
        self.act_export_pdf = QtGui.QAction("导出当前图表 PDF", self)
        self.act_export_pdf.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView))
        self.act_connect = QtGui.QAction("选择采集源", self)
        self.act_connect.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirHomeIcon))
        self.act_start = QtGui.QAction("开始监测", self)
        self.act_start.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay))
        self.act_stop = QtGui.QAction("结束监测", self)
        self.act_stop.setIcon(style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaStop))
        self.act_new.triggered.connect(self._new_batch)
        self.act_open.triggered.connect(self._open_batch)
        self.act_import.triggered.connect(self._import_csv)
        self.act_save.triggered.connect(self._save_batch_report)
        self.act_export.triggered.connect(self._export_data)
        self.act_export_pdf.triggered.connect(self._export_current_chart_pdf)
        self.act_connect.triggered.connect(self._connect_device)
        self.act_start.triggered.connect(self._begin_monitoring)
        self.act_stop.triggered.connect(self._end_monitoring)
        self.act_info = QtGui.QAction("批次信息", self)
        self.act_info.triggered.connect(self._edit_batch_info)
        self.act_replay = QtGui.QAction("烘焙回放", self)
        self.act_profile = QtGui.QAction("存为参考曲线", self)
        self.act_compare = QtGui.QAction("曲线对比", self)
        self.act_tp = QtGui.QAction("自动识别回温点", self)
        self.act_set_tp = QtGui.QAction("手动设定回温点", self)
        self.act_analyze = QtGui.QAction("曲线分析", self)
        self.act_channels = QtGui.QAction("通道映射", self)
        self.act_theme = QtGui.QAction("切换主题", self)
        self.act_background = QtGui.QAction("加载背景曲线", self)
        self.act_clear_background = QtGui.QAction("清除背景曲线", self)
        self.act_display = QtGui.QAction("显示设置", self)
        self.act_open_debug_logs = QtGui.QAction("打开调试日志目录", self)
        self.act_export_debug_bundle = QtGui.QAction("导出调试诊断包", self)
        self.act_replay.triggered.connect(self._open_replay)
        self.act_profile.triggered.connect(self._save_current_as_profile)
        self.act_compare.triggered.connect(self._open_compare)
        self.act_tp.triggered.connect(self._detect_tp)
        self.act_set_tp.triggered.connect(self._set_tp_manually)
        self.act_analyze.triggered.connect(self._show_analysis)
        self.act_channels.triggered.connect(self._edit_channel_mapping)
        self.act_theme.triggered.connect(self._choose_theme)
        self.act_background.triggered.connect(self._load_background_curve)
        self.act_clear_background.triggered.connect(self._clear_background_curve)
        self.act_display.triggered.connect(self._edit_display_settings)
        self.act_open_debug_logs.triggered.connect(self._open_debug_log_dir)
        self.act_export_debug_bundle.triggered.connect(self._export_debug_bundle)
        tb.addAction(self.act_start)
        tb.addAction(self.act_stop)
        tb.addSeparator()
        tb.addAction(self.act_new)
        tb.addAction(self.act_connect)
        tb.addSeparator()
        self.batch_toolbar_button = self._toolbar_menu_button(
            "批次菜单",
            (self.act_open, self.act_import, self.act_save, self.act_export, self.act_export_pdf, self.act_info),
        )
        self.display_toolbar_button = self._toolbar_menu_button(
            "显示菜单",
            (
                self.act_background,
                self.act_clear_background,
                self.act_display,
                self.act_theme,
                self.act_open_debug_logs,
                self.act_export_debug_bundle,
            ),
        )
        self.tools_toolbar_button = self._toolbar_menu_button(
            "曲线工具",
            (self.act_replay, self.act_profile, self.act_compare, self.act_tp, self.act_set_tp, self.act_analyze, self.act_channels),
        )
        tb.addWidget(self.batch_toolbar_button)
        tb.addWidget(self.display_toolbar_button)
        tb.addWidget(self.tools_toolbar_button)
        # The workbench navigation and bottom action bar replace this legacy,
        # duplicate toolbar. Keep every command available through the menus.
        tb.hide()

    def _toolbar_menu_button(self, text: str, actions) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton(self)
        button.setText(text)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
        menu = QtWidgets.QMenu(button)
        for action in actions:
            menu.addAction(action)
        button.setMenu(menu)
        return button

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("mainWorkbench")
        central.setProperty("uiRole", "workbench")
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(self._ui_value(8), self._ui_value(6), self._ui_value(8), self._ui_value(6))
        main_layout.setSpacing(self._ui_value(6))
        self.top_nav = self._build_top_nav()
        self.top_nav.setFixedHeight(self._ui_value(34))
        for module_name, module_button in self.module_nav_buttons.items():
            module_button.setVisible(module_name == "烘焙曲线")
        self.power_status_label.hide()
        self.preview_center_button.hide()
        self.theme_button.hide()
        main_layout.addWidget(self.top_nav)
        self.top_nav.hide()
        self.curve = CurveWidget(self)
        self.curve.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.event_panel = EventPanel(self)
        self.event_panel._grid.setHorizontalSpacing(self._ui_value(6))
        self.action_panel = ActionPanel(self)
        self.target_panel = TargetPanel(self.yellow_target_c, self.fc_target_c, self)
        target_module_badge = self.target_panel.findChild(QtWidgets.QLabel, "moduleBadge")
        if target_module_badge is not None:
            target_module_badge.clear()
            target_module_badge.hide()
        self.current_data_label = QtWidgets.QLabel("等待采集数据")
        self.current_data_label.setObjectName("hintLabel")
        self.current_data_label.setProperty("uiRole", "secondaryText")
        self.current_data_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        chart_control_bar = self._build_chart_control_bar()
        self.export_pdf_button.hide()
        self.curve_width_button.hide()

        self.process_strip = self._build_process_strip()
        main_layout.addWidget(self.process_strip)
        self.process_strip.hide()
        self.lcd_band = self._build_lcd_band()
        self.workspace_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.workspace_splitter.setObjectName("mainPanelSplitter")
        self.workspace_splitter.setProperty("uiRole", "mainWorkspace")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(self._ui_value(6))
        self.workspace_splitter.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        self.panel_splitter = self.workspace_splitter

        chart_card = QtWidgets.QFrame()
        chart_card.setObjectName("chartCard")
        chart_card.setProperty("uiRole", "primaryWorkspace")
        chart_layout = QtWidgets.QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(self._ui_value(6), self._ui_value(6), self._ui_value(6), self._ui_value(6))
        chart_layout.setSpacing(self._ui_value(5))
        chart_layout.addWidget(self._panel_header("烘焙曲线 · 当前锅次实时", ""))
        self._update_chart_header()
        chart_layout.addWidget(chart_control_bar)
        self.curve_legend_card = self._build_curve_legend_panel()
        self.curve_legend_card.hide()
        chart_layout.addWidget(self.curve_legend_card)
        self.target_drawer = QtWidgets.QFrame()
        self.target_drawer.setObjectName("targetDrawer")
        self.target_drawer.setProperty("uiRole", "panelSecondary")
        target_drawer_layout = QtWidgets.QVBoxLayout(self.target_drawer)
        target_drawer_layout.setContentsMargins(self._ui_value(8), self._ui_value(6), self._ui_value(8), self._ui_value(6))
        target_drawer_layout.addWidget(self.target_panel)
        self.target_drawer.hide()
        chart_layout.addWidget(self.target_drawer)
        chart_content_row = QtWidgets.QHBoxLayout()
        chart_content_row.setContentsMargins(0, 0, 0, 0)
        chart_content_row.setSpacing(self._ui_value(6))
        chart_content_row.addWidget(self.lcd_band, 1)
        chart_content_row.addWidget(self.curve, 4)
        chart_layout.addLayout(chart_content_row)
        self.event_card = self._right_panel_shell("事件标记", "再次点击可撤销并重录", self.event_panel)
        self.event_card.setObjectName("eventDeck")
        self.event_card.setProperty("uiRole", "scopeEventStrip")
        self.event_card.setFixedHeight(self._ui_value(82))
        self.event_panel._layout.removeWidget(self.event_panel.note_edit)
        self.event_panel.note_edit.setPlaceholderText("下一事件备注（可选）")
        self.event_card.header_layout.insertWidget(1, self.event_panel.note_edit, 1)
        chart_layout.addWidget(self.event_card)
        self.chart_card = chart_card
        self.center_workspace = self.chart_card
        self.left_host = self.chart_card

        self.right_workspace = QtWidgets.QWidget()
        self.right_workspace.setObjectName("rightRail")
        self.right_workspace.setProperty("uiRole", "rightRail")
        self.right_workspace.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Ignored)
        self.right_host = self.right_workspace
        right_layout = QtWidgets.QVBoxLayout(self.right_workspace)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.right_layout = right_layout
        self.right_panel_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.right_panel_splitter.setObjectName("rightPanelSplitter")
        self.right_panel_splitter.setProperty("uiRole", "rightPanelStack")
        self.right_panel_splitter.setChildrenCollapsible(False)
        self.right_panel_splitter.setHandleWidth(self._ui_value(4))
        self.workflow_panel = WorkflowPanel(self)
        self.manual_operation_card = self._right_panel_shell("人工操作", "仅记录 · 不控机", self.action_panel)
        self.archive_summary_panel = ArchiveSummaryPanel(self)
        for panel in (self.manual_operation_card, self.workflow_panel, self.archive_summary_panel):
            panel.setMinimumHeight(0)
            panel.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
            self.right_panel_splitter.addWidget(panel)
        self.workflow_panel.hide()
        self.archive_summary_panel.hide()
        self.right_panel_splitter.setStretchFactor(0, 1)
        self.right_panel_splitter.setStretchFactor(1, 1)
        self.right_panel_splitter.setStretchFactor(2, 1)
        right_layout.addWidget(self.right_panel_splitter)
        self.manual_operation_layout = self.manual_operation_card.layout()
        self.manual_only_label = self.action_panel._record_notice

        self.workspace_splitter.addWidget(self.chart_card)
        self.workspace_splitter.addWidget(self.right_workspace)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        main_layout.addWidget(self.workspace_splitter, 1)
        self.bottom_action_bar = self._build_bottom_action_bar()
        main_layout.addWidget(self.bottom_action_bar)
        self.setCentralWidget(central)
        self._apply_workspace_breakpoint()
        self.statusBar().showMessage("就绪：请选择真实 COM 端口或显式选择 SIMULATION")

    def _build_process_strip(self) -> QtWidgets.QFrame:
        strip = QtWidgets.QFrame()
        strip.setObjectName("processStrip")
        strip.setProperty("uiRole", "processStrip")
        strip.setFixedHeight(self._ui_value(44))
        layout = QtWidgets.QHBoxLayout(strip)
        layout.setContentsMargins(self._ui_value(10), self._ui_value(5), self._ui_value(10), self._ui_value(5))
        layout.setSpacing(self._ui_value(8))
        self.process_batch_label = QtWidgets.QLabel("—")
        self.process_batch_label.setObjectName("processBatchLabel")
        self.process_batch_label.setProperty("uiRole", "numericSummary")
        self.process_stage_badge = QtWidgets.QLabel("待采集")
        self.process_stage_badge.setObjectName("processStageBadge")
        self.process_stage_badge.setProperty("uiRole", "statusBadge")
        self.process_stage_badge.setProperty("statusTone", "neutral")
        self.process_hint_label = self.current_data_label
        self.process_hint_label.setText("未开始采集 · 点击「开始采集」将启动采集并自动开启第 1 锅")
        self.process_stats_label = QtWidgets.QLabel("采集总时长 00:00 · 已完成 0 锅")
        self.process_stats_label.setObjectName("processStatsLabel")
        self.process_stats_label.setProperty("uiRole", "secondaryText")
        layout.addWidget(self.process_batch_label)
        layout.addWidget(self.process_stage_badge)
        layout.addWidget(self.process_hint_label, 1)
        layout.addWidget(self.process_stats_label)
        return strip

    def _build_lcd_band(self) -> QtWidgets.QFrame:
        band = QtWidgets.QFrame()
        band.setObjectName("lcdBand")
        band.setProperty("uiRole", "lcdBand")
        band.setMinimumHeight(0)
        band.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        metrics = QtWidgets.QGridLayout(band)
        metrics.setContentsMargins(self._ui_value(4), self._ui_value(4), self._ui_value(4), self._ui_value(4))
        metrics.setHorizontalSpacing(self._ui_value(6))
        metrics.setVerticalSpacing(self._ui_value(4))
        self.lcd_metrics_layout = metrics
        self.metric_bt = self._dual_metric_card("BT 豆温", "--.-", "--", tone="bt", value_unit="°C", sub_label="RoR", sub_unit="°C/min")
        self.metric_et = self._dual_metric_card("ET 环境温", "--.-", "--", tone="et", value_unit="°C", sub_label="RoR", sub_unit="°C/min")
        self.metric_it = self._dual_metric_card("IT 进风温", "--.-", "--", tone="it", value_unit="°C", sub_label="RoR", sub_unit="°C/min")
        self.metric_exhaust = self._dual_metric_card("XT 排风温", "--.-", "--", tone="exhaust", value_unit="°C", sub_label="RoR", sub_unit="°C/min")
        self.metric_ror = self._metric_card("ΔBT · °C/min", "--", tone="ror")
        self.metric_time = self._metric_card("ROAST TIME", "--:--", tone="ok")
        self.metric_tp = self._metric_card("回温点 TP", "--:--", compact=True, tone="ok")
        self.metric_tp.setParent(band)
        self.metric_tp.hide()
        self.metric_dev_time = self._dual_metric_card("发展期 DTR", "--:--", "--.- %", tone="ok")
        self.metric_ror.setParent(band)
        self.metric_ror.hide()
        self.visible_metric_cards = (
            self.metric_bt, self.metric_et, self.metric_it,
            self.metric_exhaust, self.metric_time, self.metric_dev_time,
        )
        self._arrange_lcd_cards(2)
        return band

    def _build_bottom_action_bar(self) -> QtWidgets.QFrame:
        bar = QtWidgets.QFrame()
        bar.setObjectName("bottomActionBar")
        bar.setProperty("uiRole", "primaryActions")
        bar.setFixedHeight(self._ui_value(52))
        controls = QtWidgets.QHBoxLayout(bar)
        controls.setContentsMargins(self._ui_value(8), self._ui_value(6), self._ui_value(8), self._ui_value(6))
        controls.setSpacing(self._ui_value(6))
        self.power_on_button = QtWidgets.QPushButton("ON")
        self.power_on_button.setObjectName("lifecycleOnButton")
        self.power_on_button.setToolTip("选择并连接采集设备")
        self.power_on_button.clicked.connect(lambda: self.act_connect.trigger())
        self.start_monitor_button = QtWidgets.QPushButton("▶ 开始采集")
        self.start_monitor_button.setObjectName("startMonitorButton")
        self.reset_batch_button = QtWidgets.QPushButton("结束烘焙")
        self.reset_batch_button.setObjectName("resetBatchButton")
        self.start_next_batch_button = QtWidgets.QPushButton("开始烘焙")
        self.start_next_batch_button.setObjectName("startNextBatchButton")
        self.stop_monitor_button = QtWidgets.QPushButton("■ 停止采集")
        self.stop_monitor_button.setObjectName("stopMonitorButton")
        self.start_monitor_button.clicked.connect(self._begin_monitoring)
        self.start_next_batch_button.clicked.connect(self._begin_monitoring)
        self.stop_monitor_button.clicked.connect(self._end_monitoring)
        self.reset_batch_button.clicked.connect(self._request_finish_batch)
        self.undo_event_button = QtWidgets.QPushButton("↺ 撤销")
        self.undo_event_button.setObjectName("undoEventButton")
        self.undo_event_button.clicked.connect(self._undo_latest_event)
        for button in (
            self.power_on_button, self.start_monitor_button, self.reset_batch_button,
            self.start_next_batch_button, self.stop_monitor_button, self.undo_event_button,
        ):
            controls.addWidget(button)
        self.start_next_batch_button.hide()
        self.undo_event_button.hide()
        controls.addStretch(1)
        self.bottom_status_label = QtWidgets.QLabel("统一预览框架 · 所有写操作先只读预览后执行")
        self.bottom_status_label.setObjectName("bottomStatusLabel")
        self.bottom_status_label.setProperty("uiRole", "secondaryText")
        self.bottom_status_label.hide()
        controls.addWidget(self.bottom_status_label)
        return bar

    def _right_panel_shell(self, title: str, subtitle: str, content: QtWidgets.QWidget) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("rightRailPanel")
        card.setProperty("uiRole", "panel")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(self._ui_value(4), self._ui_value(6), self._ui_value(4), self._ui_value(6))
        layout.setSpacing(self._ui_value(4))
        header = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setProperty("uiRole", "sectionTitle")
        subtitle_label = QtWidgets.QLabel(subtitle)
        subtitle_label.setObjectName("rightPanelSubtitle")
        subtitle_label.setProperty("uiRole", "secondaryText")
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(subtitle_label)
        layout.addLayout(header)
        layout.addWidget(content, 1)
        card.subtitle_label = subtitle_label
        card.header_layout = header
        card.title_label = title_label
        return card

    def _arrange_lcd_cards(self, columns: int) -> None:
        while self.lcd_metrics_layout.count():
            self.lcd_metrics_layout.takeAt(0)
        for column in range(self.lcd_metrics_layout.columnCount()):
            self.lcd_metrics_layout.setColumnStretch(column, 0)
            self.lcd_metrics_layout.setColumnMinimumWidth(column, 0)
        self.lcd_metrics_layout.setVerticalSpacing(self._ui_value(4))
        for index, metric in enumerate(self.visible_metric_cards):
            metric.layout().setContentsMargins(
                self._ui_value(4), self._ui_value(4), self._ui_value(4), self._ui_value(4),
            )
            self.lcd_metrics_layout.addWidget(metric, index // columns, index % columns)
            self.lcd_metrics_layout.setRowStretch(index // columns, 1)
        for column in range(columns):
            self.lcd_metrics_layout.setColumnStretch(column, 1)

    def _undo_latest_event(self) -> None:
        if self.workflow.stage is not RoastStage.ROASTING:
            self.statusBar().showMessage("仅烘焙中可撤销")
            return
        cancellable = [item for item in self.events if item.get("event_type") != "DROP"]
        if not cancellable:
            self.statusBar().showMessage("无可撤销事件")
            return
        latest = max(cancellable, key=lambda item: float(item.get("time_s") or 0.0))
        self._on_event_cancelled(str(latest.get("event_type") or ""))

    def _build_curve_legend_panel(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("curveLegendBand")
        card.setProperty("uiRole", "legendBand")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(self._ui_value(6), self._ui_value(3), self._ui_value(6), self._ui_value(3))
        layout.setSpacing(self._ui_value(2))
        self.curve_visibility_checks = {}
        check_host = QtWidgets.QWidget()
        check_host.setObjectName("curveLegendGrid")
        grid = QtWidgets.QGridLayout(check_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self._ui_value(6))
        grid.setVerticalSpacing(self._ui_value(4))
        labels = list(self.curve.curve_labels().items())
        def legend_color(label: str) -> str:
            if "燃气" in label:
                return "#f5d442"
            if "风门" in label:
                return "#35c98e"
            if "转速" in label:
                return "#ec6ba9"
            if "进风" in label:
                return "#4fb3ff"
            if "炉温" in label:
                return "#e8544f"
            if "排风" in label:
                return "#d7a84b"
            if "背景" in label:
                return "#8a94a6"
            return "#ff8c2f"
        for index, (key, label) in enumerate(labels):
            check = QtWidgets.QCheckBox(label)
            check.setObjectName("curveLegendCheck")
            color = legend_color(label)
            check.setStyleSheet(
                "QCheckBox::indicator { width: 12px; height: 12px; border-radius: 2px; "
                f"border: 1px solid {color}; background: transparent; }}"
                f"QCheckBox::indicator:checked {{ background: {color}; }}"
            )
            check.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
            check.setMaximumHeight(self._ui_value(22))
            check.setChecked(self.curve.curve_visibility()[key])
            check.toggled.connect(lambda checked, curve_key=key: self._set_curve_visibility(curve_key, checked))
            self.curve_visibility_checks[key] = check
        self.curve_legend_layout = layout
        self.curve_legend_grid = grid
        layout.addWidget(check_host)
        self._arrange_curve_legend(6)
        return card

    def _arrange_curve_legend(self, columns: int) -> None:
        while self.curve_legend_grid.count():
            self.curve_legend_grid.takeAt(0)
        checks = list(self.curve_visibility_checks.values())
        for index, check in enumerate(checks):
            self.curve_legend_grid.addWidget(check, index // columns, index % columns)
        for column in range(columns):
            self.curve_legend_grid.setColumnStretch(column, 1)

    def _apply_workspace_breakpoint(self) -> None:
        if not hasattr(self, "workspace_splitter"):
            return
        width = self.width()
        short_height = self.height() < 900
        compact_width = width <= 1180
        narrow_width = width <= 980
        self.menu_brand_action.setText(
            "周周 ROAST" if compact_width else "周周 ROAST · 烘焙曲线"
        )
        self.menu_status_action.setVisible(not narrow_width)
        self.action_panel.set_compact_mode(short_height or compact_width)
        self.event_panel.set_compact_mode(True)
        self.workflow_panel.set_compact_mode(short_height)
        self.archive_summary_panel.set_compact_mode(short_height)
        self.event_panel.set_button_columns(8 if width >= 900 else 4)
        self.current_data_label.setWordWrap(False)
        self.bottom_status_label.setVisible(False)
        self.event_card.subtitle_label.setVisible(not (compact_width or short_height))
        self.manual_operation_card.subtitle_label.setVisible(not (compact_width or short_height))
        self.chart_subtitle_label.setVisible(not compact_width)
        self.process_hint_label.setVisible(not narrow_width)
        for chip in (self.forecast_yellow_chip, self.forecast_fc_chip):
            chip.setVisible(not (compact_width or short_height))
        for control in (self.ror_window_combo, self.line_width_label, self.line_width_combo):
            control.setVisible(not narrow_width)
        self.curve_width_button.setVisible(False)
        self._arrange_curve_legend(6 if width > 1180 else 4 if width > 980 else 3)
        self._arrange_lcd_cards(1)
        self.lcd_band.setFixedWidth(self._ui_value(180))
        self.lcd_band.setProperty("compactLcd", narrow_width)
        self.lcd_band.style().unpolish(self.lcd_band)
        self.lcd_band.style().polish(self.lcd_band)
        self.workspace_splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
        right_width = self._ui_value(260 if compact_width else 286)
        self.right_workspace.setFixedWidth(right_width)
        self.workspace_splitter.setSizes([max(self._ui_value(420), width - right_width), right_width])
        if short_height:
            self.right_panel_splitter.setSizes([
                self._ui_value(190), self._ui_value(300), 0,
            ])
        else:
            self.right_panel_splitter.setSizes([
                self._ui_value(220), self._ui_value(400), 0,
            ])

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_workspace_breakpoint()

    def _build_chart_control_bar(self) -> QtWidgets.QFrame:
        bar = QtWidgets.QFrame()
        bar.setObjectName("chartControlBar")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(self._ui_value(8), self._ui_value(5), self._ui_value(8), self._ui_value(5))
        layout.setSpacing(self._ui_value(6))

        time_range_label = QtWidgets.QLabel("时间范围")
        time_range_label.setObjectName("hintLabel")
        time_range_label.setToolTip("起止时间以秒为单位；自动跟随时，范围随最新数据延伸")
        layout.addWidget(time_range_label)
        self.time_start_spin = QtWidgets.QDoubleSpinBox()
        self.time_start_spin.setRange(0.0, 99999.0)
        self.time_start_spin.setDecimals(1)
        self.time_start_spin.setSingleStep(10.0)
        self.time_start_spin.setSuffix(" s")
        self.time_start_spin.setFixedWidth(self._ui_value(112))
        self.time_start_spin.setValue(0.0)
        self.time_start_spin.setAccessibleName("曲线起始时间（秒）")
        self.time_end_spin = QtWidgets.QDoubleSpinBox()
        self.time_end_spin.setRange(0.0, 99999.0)
        self.time_end_spin.setDecimals(1)
        self.time_end_spin.setSingleStep(10.0)
        self.time_end_spin.setSuffix(" s")
        self.time_end_spin.setFixedWidth(self._ui_value(112))
        self.time_end_spin.setValue(600.0)
        self.time_end_spin.setAccessibleName("曲线结束时间（秒）")
        self.apply_time_range_button = QtWidgets.QPushButton("应用范围")
        self.apply_time_range_button.clicked.connect(self._apply_time_range)
        layout.addWidget(self.time_start_spin)
        layout.addWidget(QtWidgets.QLabel("至"))
        layout.addWidget(self.time_end_spin)
        layout.addWidget(self.apply_time_range_button)
        self.ror_window_combo = QtWidgets.QComboBox()
        for seconds in (5, 10, 15, 30):
            self.ror_window_combo.addItem(f"RoR 窗口 {seconds} 秒", seconds)
        self.ror_window_combo.setCurrentIndex(1)
        self.ror_window_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.ror_window_combo.currentIndexChanged.connect(self._on_ror_window_changed)
        self.follow_button = QtWidgets.QToolButton()
        self.follow_button.setText("自动跟随")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setToolTip("随实时数据向右扩展横轴，持续显示最新数据点")
        self.follow_button.toggled.connect(self.curve.set_auto_follow)
        layout.addWidget(self.ror_window_combo)
        layout.addWidget(self.follow_button)

        self.target_toggle_button = QtWidgets.QToolButton()
        self.target_toggle_button.setObjectName("targetToggleButton")
        self.target_toggle_button.setText("目标")
        self.target_toggle_button.setCheckable(True)
        self.target_toggle_button.setToolTip("展开或收起里程碑目标与 ETA 预测")
        self.target_toggle_button.toggled.connect(
            lambda checked: self.target_drawer.setVisible(checked)
        )
        layout.addWidget(self.target_toggle_button)

        self.forecast_yellow_chip = QtWidgets.QPushButton("预计转黄：等待数据")
        self.forecast_yellow_chip.setObjectName("forecastChip")
        self.forecast_yellow_chip.setEnabled(False)
        self.forecast_fc_chip = QtWidgets.QPushButton("预计一爆：等待数据")
        self.forecast_fc_chip.setObjectName("forecastChip")
        self.forecast_fc_chip.setEnabled(False)
        layout.addWidget(self.forecast_yellow_chip)
        layout.addWidget(self.forecast_fc_chip)

        self.axis_settings_button = QtWidgets.QPushButton("坐标")
        self.axis_settings_button.setObjectName("axisSettingsButton")
        self.axis_settings_button.setFixedWidth(self._ui_value(48))
        self.axis_settings_button.setToolTip("设置 X、左 Y、右 Y 的最小值、最大值、跨度和主刻度")
        self.axis_settings_button.clicked.connect(self._open_axis_settings)
        layout.addWidget(self.axis_settings_button)

        self.export_pdf_button = QtWidgets.QPushButton("PDF")
        self.export_pdf_button.setObjectName("exportChartPdfButton")
        self.export_pdf_button.setFixedWidth(self._ui_value(42))
        self.export_pdf_button.clicked.connect(self._export_current_chart_pdf)
        layout.addWidget(self.export_pdf_button)

        self.curve_visibility_button = QtWidgets.QToolButton()
        self.curve_visibility_button.setObjectName("curveMenuButton")
        self.curve_visibility_button.setText("曲线显示")
        self.curve_visibility_button.setFixedWidth(self._ui_value(82))
        self.curve_visibility_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.curve_visibility_menu = QtWidgets.QMenu(self.curve_visibility_button)
        self.curve_visibility_actions = {}
        visibility = self.curve.curve_visibility()
        section_starts = {
            "BT": "温度 · 实线 / 左轴",
            "BT_ROR": "RoR · 虚线 / 右轴",
            "PRED_ROR": "预测",
            "GAS": "工艺参数 · 归一化 / 左轴",
            "BG_BT": "参考曲线",
        }
        channel_colors = {
            "BT": "#ff8c2f", "ET": "#e8544f", "IT": "#4fb3ff",
            "EXHAUST": "#d7a84b", "PRED": "#ff8c2f",
            "GAS": "#f5d442", "DAMPER": "#35c98e", "RPM": "#ec6ba9",
            "BG": "#8a94a6",
        }
        for key, label in self.curve.curve_labels().items():
            if key in section_starts:
                self.curve_visibility_menu.addSection(section_starts[key])
            action = self.curve_visibility_menu.addAction(label)
            swatch = QtGui.QPixmap(self._ui_value(32), self._ui_value(16))
            swatch.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(swatch)
            color_key = key.split("_")[0]
            pen = QtGui.QPen(QtGui.QColor(channel_colors[color_key]))
            pen.setWidthF(float(self._ui_value(2)))
            if "ROR" in key or key.startswith("BG_"):
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(1, swatch.height() // 2, swatch.width() - 1, swatch.height() // 2)
            painter.end()
            action.setIcon(QtGui.QIcon(swatch))
            action.setCheckable(True)
            action.setChecked(visibility[key])
            action.toggled.connect(
                lambda checked, curve_key=key: self._set_curve_visibility(curve_key, checked)
            )
            self.curve_visibility_actions[key] = action
        self.curve_visibility_button.setMenu(self.curve_visibility_menu)
        layout.addWidget(self.curve_visibility_button)

        self.line_width_label = QtWidgets.QLabel("线宽")
        self.line_width_label.setObjectName("hintLabel")
        layout.addWidget(self.line_width_label)
        self.line_width_combo = QtWidgets.QComboBox()
        self.line_width_combo.addItem("细", 0.75)
        self.line_width_combo.addItem("标准", 1.0)
        self.line_width_combo.addItem("粗", 1.5)
        self.line_width_combo.setCurrentIndex(1)
        self.line_width_combo.currentIndexChanged.connect(self._on_line_width_changed)
        layout.addWidget(self.line_width_combo)

        self.curve_width_button = QtWidgets.QToolButton()
        self.curve_width_button.setText("单曲线线宽")
        self.curve_width_button.setFixedWidth(self._ui_value(92))
        self.curve_width_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.curve_width_menu = QtWidgets.QMenu(self.curve_width_button)
        self.curve_width_menu.setTitle("单曲线线宽")
        self.curve_visibility_menu.addSeparator()
        self.curve_visibility_menu.addMenu(self.curve_width_menu)
        self.curve_width_actions = {}
        for key, label in self.curve.curve_labels().items():
            curve_menu = self.curve_width_menu.addMenu(label)
            for width_label, width_scale in (("细", 0.75), ("标准", 1.0), ("粗", 1.5)):
                action = curve_menu.addAction(width_label)
                action.setCheckable(True)
                action.setChecked(width_scale == 1.0)
                action.triggered.connect(
                    lambda _checked, curve_key=key, scale=width_scale: self._set_curve_specific_line_width(curve_key, scale)
                )
                self.curve_width_actions[(key, width_scale)] = action
        self.curve_width_button.setMenu(self.curve_width_menu)
        layout.addWidget(self.curve_width_button)
        layout.addStretch(1)

        self.curve.time_range_changed.connect(self._on_curve_time_range_changed)
        self.curve.temp_plot.setXRange(0.0, 600.0, padding=0)
        self._update_chart_pdf_availability()
        return bar

    def _set_curve_visibility(self, key: str, visible: bool) -> None:
        self.curve.set_curve_visibility(key, visible)
        action = self.curve_visibility_actions.get(key)
        if action is not None and action.isChecked() != bool(visible):
            blocker = QtCore.QSignalBlocker(action)
            action.setChecked(bool(visible))
            del blocker
        check = getattr(self, "curve_visibility_checks", {}).get(key)
        if check is not None and check.isChecked() != bool(visible):
            blocker = QtCore.QSignalBlocker(check)
            check.setChecked(bool(visible))
            del blocker

    @staticmethod
    def _setting_bool(value, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _setting_float(value, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def _restore_axis_settings(self) -> None:
        defaults = self.curve.axis_settings()
        auto_defaults = self.curve.axis_auto_states()
        if not any(key.startswith("axis/") for key in self._settings.allKeys()):
            return
        restored = {}
        for axis in ("x", "left", "right"):
            values = defaults[axis]
            minimum = self._setting_float(self._settings.value(f"axis/{axis}/min"), values["min"])
            maximum = self._setting_float(self._settings.value(f"axis/{axis}/max"), values["max"])
            major = self._setting_float(self._settings.value(f"axis/{axis}/major"), values["major"])
            restored[axis] = {
                "min": minimum,
                "max": maximum,
                "span": maximum - minimum,
                "major": major,
                "auto": self._setting_bool(self._settings.value(f"axis/{axis}/auto"), auto_defaults[axis]),
            }
        try:
            self.curve.apply_axis_settings(restored)
        except (KeyError, TypeError, ValueError):
            self._settings.remove("axis")
            self._settings.sync()

    def _save_axis_settings(self) -> None:
        settings = self.curve.axis_settings()
        auto_states = self.curve.axis_auto_states()
        for axis in ("x", "left", "right"):
            values = settings[axis]
            self._settings.setValue(f"axis/{axis}/min", float(values["min"]))
            self._settings.setValue(f"axis/{axis}/max", float(values["max"]))
            self._settings.setValue(f"axis/{axis}/major", float(values["major"]))
            self._settings.setValue(f"axis/{axis}/auto", bool(auto_states[axis]))
        self._settings.sync()

    def _on_curve_time_range_changed(self, left: float, right: float) -> None:
        start_blocker = QtCore.QSignalBlocker(self.time_start_spin)
        end_blocker = QtCore.QSignalBlocker(self.time_end_spin)
        self.time_start_spin.setValue(max(0.0, float(left)))
        self.time_end_spin.setValue(max(0.0, float(right)))
        del start_blocker, end_blocker

    def _apply_time_range(self) -> None:
        left = float(self.time_start_spin.value())
        right = float(self.time_end_spin.value())
        if right <= left:
            self.statusBar().showMessage("时间范围无效：结束时间必须大于开始时间，已保留原范围")
            return
        self.follow_button.setChecked(False)
        try:
            self.curve.set_time_range(left, right)
        except ValueError as exc:
            self.statusBar().showMessage(f"时间范围无效：{exc}，已保留原范围")
            return
        if self.samples and right > max(float(item.get("time_s", 0.0)) for item in self.samples):
            self.statusBar().showMessage("已应用时间范围；当前数据未覆盖全部区间，超出部分留空")
        else:
            self.statusBar().showMessage(f"已应用时间范围：{left:.1f} - {right:.1f} 秒")

    def _open_axis_settings(self) -> None:
        dialog = AxisSettingsDialog(self.curve.axis_settings(), self.curve.axis_auto_states(), self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            self.curve.apply_axis_settings(dialog.values())
        except ValueError as exc:
            self.statusBar().showMessage(f"坐标轴设置无效：{exc}；已保留旧视图")
            return
        self._save_axis_settings()
        self.follow_button.setChecked(self.curve.auto_follow)
        self._on_curve_time_range_changed(*self.curve.time_range())
        self.statusBar().showMessage("三轴范围与主刻度已应用")

    def _update_chart_pdf_availability(self) -> None:
        available = bool(self.background_profile_name or getattr(self.curve, "_background_has_rows", False) or self._roast_finished)
        reason = "可导出：已加载背景/参考曲线或当前烘焙已结束" if available else "需先加载背景/参考曲线，或结束当前烘焙"
        for control in (getattr(self, "export_pdf_button", None), getattr(self, "act_export_pdf", None)):
            if control is not None:
                control.setEnabled(available)
                control.setToolTip(reason)

    def _refresh_pdf_export_state(self) -> None:
        self._update_chart_pdf_availability()

    def _export_current_chart_pdf(self) -> None:
        if not (self.background_profile_name or getattr(self.curve, "_background_has_rows", False) or self._roast_finished):
            self.statusBar().showMessage("PDF 导出不可用：请先加载背景/参考曲线，或结束当前烘焙")
            return
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出当前图表 PDF",
            str(self._default_open_dir / "烘焙图表.pdf"),
            "PDF 文件 (*.pdf)",
        )
        if not target:
            return
        snapshot = self._build_write_preview_snapshot(
            "导出曲线",
            "✓ 确认导出当前图表 PDF",
            export_summary={
                "文件": str(target),
                "格式": "PDF",
                "采样点": f"{len(self.samples)} 点",
                "背景曲线": self.background_profile_name or "无",
            },
            export_rows=self._export_preview_rows(),
            after="确认后才会渲染并写入 PDF；取消或关闭不会创建文件。",
        )

        def write_pdf() -> None:
            self.export_pdf(target)
            self._default_open_dir = Path(target).resolve().parent
            self.statusBar().showMessage(f"已导出当前图表 PDF：{target}")

        self._show_write_preview(snapshot, write_pdf, "PDF 导出")

    def export_pdf(self, target: str | Path) -> None:
        if not (self.background_profile_name or getattr(self.curve, "_background_has_rows", False) or self._roast_finished):
            raise RuntimeError("PDF 导出需要背景/参考曲线，或已经结束当前烘焙")
        export_chart_pdf(target, self.curve, f"HB 烘焙图表 · {self._batch_id or '-'}")

    def _on_line_width_changed(self) -> None:
        scale = float(self.line_width_combo.currentData())
        self.curve.set_line_width(scale)
        self._sync_curve_width_actions()
        self.statusBar().showMessage(f"曲线线宽已切换：{self.line_width_combo.currentText()}")

    def _set_curve_specific_line_width(self, key: str, scale: float) -> None:
        self.curve.set_curve_line_width(key, scale)
        for width_scale in (0.75, 1.0, 1.5):
            action = self.curve_width_actions.get((key, width_scale))
            if action is not None:
                action.setChecked(width_scale == scale)
        self.statusBar().showMessage(f"{self.curve.curve_labels()[key]}线宽已切换")

    def _sync_curve_width_actions(self) -> None:
        for (key, scale), action in self.curve_width_actions.items():
            action.setChecked(abs(self.curve.curve_line_width(key) - scale) < 1e-6)

    def _metric_card(self, label: str, value: str, compact: bool = False, tone: str = "accent") -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("metricCard")
        card.setProperty("accentTone", tone)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(self._ui_value(6), self._ui_value(6), self._ui_value(6), self._ui_value(6))
        layout.setSpacing(2)
        card.setMinimumWidth(self._ui_value(126))
        if compact:
            card.setMinimumWidth(self._ui_value(64))
            card.setMaximumWidth(self._ui_value(90))
            card.setMaximumHeight(self._ui_value(56))
            layout.setContentsMargins(self._ui_value(5), self._ui_value(3), self._ui_value(5), self._ui_value(3))
            layout.setSpacing(1)
        card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        label_widget = QtWidgets.QLabel(label)
        if compact:
            label_widget.setObjectName("smallMetricLabel")
        else:
            label_widget.setObjectName("metricLabel")
        value_widget = QtWidgets.QLabel(value)
        if compact:
            value_widget.setObjectName("smallMetricValue")
        else:
            value_widget.setObjectName("metricValue")
            value_widget.setMinimumWidth(self._ui_value(88))
        value_widget.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        card.value_widget = value_widget
        return card

    def _dual_metric_card(
        self,
        label: str,
        value: str,
        sub_value: str,
        tone: str = "accent",
        value_unit: str = "",
        sub_label: str = "",
        sub_unit: str = "",
    ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("metricCard")
        card.setProperty("accentTone", tone)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(self._ui_value(6), self._ui_value(6), self._ui_value(6), self._ui_value(6))
        layout.setSpacing(1)
        card.setMinimumWidth(self._ui_value(154))
        card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        label_widget = QtWidgets.QLabel(label)
        label_widget.setObjectName("metricLabel")
        value_widget = QtWidgets.QLabel(value)
        value_widget.setObjectName("metricValue")
        sub_value_widget = QtWidgets.QLabel(sub_value)
        sub_value_widget.setObjectName("metricSubValue")
        value_widget.setMinimumWidth(self._ui_value(72 if value_unit else 88))
        sub_value_widget.setMinimumWidth(self._ui_value(48 if sub_unit else 100))
        value_widget.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        sub_value_widget.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(label_widget)
        if value_unit or sub_label or sub_unit:
            value_row = QtWidgets.QHBoxLayout()
            value_row.setContentsMargins(0, 0, 0, 0)
            value_row.setSpacing(self._ui_value(3))
            value_row.addStretch(1)
            value_row.addWidget(value_widget)
            value_unit_widget = QtWidgets.QLabel(value_unit)
            value_unit_widget.setObjectName("metricUnit")
            value_unit_widget.setMinimumWidth(self._ui_value(18 if value_unit else 0))
            value_row.addWidget(value_unit_widget)
            layout.addLayout(value_row)

            sub_row = QtWidgets.QHBoxLayout()
            sub_row.setContentsMargins(0, 0, 0, 0)
            sub_row.setSpacing(self._ui_value(3))
            sub_label_widget = QtWidgets.QLabel(sub_label)
            sub_label_widget.setObjectName("metricSubLabel")
            sub_row.addWidget(sub_label_widget)
            sub_row.addStretch(1)
            sub_row.addWidget(sub_value_widget)
            sub_unit_widget = QtWidgets.QLabel(sub_unit)
            sub_unit_widget.setObjectName("metricUnit")
            sub_unit_widget.setMinimumWidth(self._ui_value(60 if sub_unit else 0))
            sub_row.addWidget(sub_unit_widget)
            layout.addLayout(sub_row)
            card.value_unit_widget = value_unit_widget
            card.sub_label_widget = sub_label_widget
            card.sub_unit_widget = sub_unit_widget
        else:
            layout.addWidget(value_widget)
            layout.addWidget(sub_value_widget)
        card.value_widget = value_widget
        card.sub_value_widget = sub_value_widget
        return card

    def _on_ror_window_changed(self) -> None:
        self.ror_window_seconds = int(self.ror_window_combo.currentData())
        history = deque()
        for sample in self.samples:
            if sample.get("gap_before"):
                history.clear()
            history.append(sample)
            while history and float(history[0]["time_s"]) < float(sample["time_s"]) - self.ror_window_seconds:
                history.popleft()
            sample.update(calculate_ror(history, window=self.ror_window_seconds))
        self.curve.set_data(self.samples, predict_ror(self.samples, future_seconds=30), self.events)
        self.statusBar().showMessage(f"RoR 计算窗口已切换为 {self.ror_window_seconds} 秒")

    def _start_debug_logger(self) -> None:
        if self.selected_source == "SIMULATION":
            return
        try:
            self._debug_log_path = default_debug_log_path(
                source=self.selected_source,
                batch_id=self._batch_id,
                profile=self.selected_profile,
            )
            self._debug_logger = SessionDebugLogger(self._debug_log_path)
            self._debug_logger.start(
                batch_id=self._batch_id,
                source=self.selected_source,
                source_display=self.selected_source_display,
                profile=self.selected_profile,
                baudrate=self.selected_baudrate,
                source_details=self.selected_source_meta,
            )
            self._debug_logger.log_event(
                "monitor_request",
                {
                    "selected_source": self.selected_source,
                    "selected_source_display": self.selected_source_display,
                    "selected_profile": self.selected_profile,
                    "selected_baudrate": self.selected_baudrate,
                },
            )
            self.statusBar().showMessage(f"调试日志已开启：{self._debug_log_path}")
        except Exception as exc:
            self._debug_logger = None
            self.statusBar().showMessage(f"调试日志启动失败：{exc}")

    def _stop_debug_logger(self, reason: str = "normal") -> None:
        if self._debug_logger is None:
            return
        self._record_debug_event("monitor_stop", {"reason": reason})
        try:
            self._debug_logger.stop(reason)
        except Exception:
            pass
        self._debug_logger = None

    def _record_debug_event(self, event: str, payload: dict) -> None:
        if self._debug_logger:
            self._debug_logger.log_event(event, payload)

    def _on_raw_received(self, raw: str) -> None:
        if self._debug_logger and raw:
            self._debug_logger.log_raw(
                raw,
                source=self.selected_source,
                source_display=self.selected_source_display,
                profile=self.selected_profile,
                baudrate=self.selected_baudrate,
                raw_len=len(raw),
            )

    def _on_serial_command(self, command: str) -> None:
        self._record_debug_event(
            "serial_command",
            {
                "command": command,
                "source_display": self.selected_source_display,
                "source": self.selected_source,
                "profile": self.selected_profile,
                "baudrate": self.selected_baudrate,
                "raw_len": len(command) if isinstance(command, str) else 0,
            },
        )

    def _open_debug_log_dir(self) -> None:
        try:
            debug_dir = default_debug_log_dir()
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(debug_dir)))
        except Exception as exc:
            self.statusBar().showMessage(f"打开调试日志目录失败：{exc}")

    def _export_debug_bundle(self) -> None:
        logger = self._debug_logger
        if logger is None:
            default_dir = default_debug_log_dir()
            candidates = sorted(default_dir.glob("roast_debug_*.jsonl"))
            if not candidates:
                self.statusBar().showMessage("当前无运行中的调试会话，且日志目录中无可打包文件")
                return
            target = self._debug_log_path if self._debug_log_path in candidates else candidates[-1]
            default_name = f"{target.stem}"
            output, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "导出最近调试诊断包",
                str(default_dir / "bundle" / f"{default_name}_bundle.zip"),
                "ZIP (*.zip)",
            )
            if not output:
                return
            try:
                bundle_path = SessionDebugLogger.export_log_bundle(target, output)
                self.statusBar().showMessage(f"调试诊断包已导出：{bundle_path}")
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(bundle_path.parent)))
            except Exception as exc:
                self.statusBar().showMessage(f"导出调试诊断包失败：{exc}")
            return

        default_name = f"roast_debug_bundle_{self._debug_log_path.stem if self._debug_log_path is not None else 'session'}"
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出调试诊断包",
            str(default_debug_log_dir() / "bundle" / f"{default_name}.zip"),
            "ZIP (*.zip)",
        )
        if not output:
            return
        try:
            bundle_path = logger.export_bundle(output)
            self.statusBar().showMessage(f"调试诊断包已导出：{bundle_path}")
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(bundle_path.parent)))
        except Exception as exc:
            self.statusBar().showMessage(f"导出调试诊断包失败：{exc}")

    def _bind(self) -> None:
        self.manager.raw_received.connect(self._on_raw_received)
        if hasattr(self.manager, "command_sent"):
            self.manager.command_sent.connect(self._on_serial_command)
        self.manager.sample_received.connect(self._on_sample_raw)
        self.manager.error.connect(self._on_error)
        if hasattr(self.manager, "parse_error"):
            self.manager.parse_error.connect(self._on_parse_error)
        if hasattr(self.manager, "parse_error_detail"):
            self.manager.parse_error_detail.connect(self._on_parse_error_detail)
        self.manager.state_changed.connect(self._on_state_changed)
        self.event_panel.event_requested.connect(self._on_event_requested)
        self.event_panel.event_cancelled.connect(self._on_event_cancelled)
        self.action_panel.action_added.connect(self._on_action_added)
        self.target_panel.targets_changed.connect(self._on_targets_changed)
        self.event_panel.set_current_time(self._get_time_s)
        self.action_panel.set_current_time(self._get_time_s)
        self._set_session_active(False)

    def _clear_connection_retry(self) -> None:
        self._reconnect_attempts = 0
        if self._reconnect_timer.isActive():
            self._reconnect_timer.stop()

    def _schedule_reconnect(self) -> None:
        if not self.session_active or self.selected_source == "SIMULATION":
            return
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self._abort_monitoring_after_reconnect_failure()
            return
        if self._reconnect_timer.isActive():
            return
        self._record_debug_event("reconnect_scheduled", {"attempt": self._reconnect_attempts + 1})
        self._reconnect_timer.start()

    def _connect_selected_source(self) -> None:
        try:
            self.manager.connect(
                self.selected_source,
                baudrate=self.selected_baudrate,
                profile=self.selected_profile,
            )
        except TypeError as exc:
            if "profile" not in str(exc):
                raise
            self.manager.connect(self.selected_source, baudrate=self.selected_baudrate)

    def _attempt_reconnect(self) -> None:
        if not self.session_active or self.selected_source == "SIMULATION":
            return
        self._reconnect_attempts += 1
        self._set_session_status(f"采集中（重连中 {self._reconnect_attempts}/{self._max_reconnect_attempts}）")
        self.statusBar().showMessage(f"重连采集源：{self.selected_source}（{self._reconnect_attempts}/{self._max_reconnect_attempts}）")
        self._record_debug_event(
            "reconnect_attempt",
            {
                "attempt": self._reconnect_attempts,
                "max_attempts": self._max_reconnect_attempts,
                "source": self.selected_source,
                "baudrate": self.selected_baudrate,
                "profile": self.selected_profile,
            },
        )
        self._rebase_capture_on_next_sample = self._last_capture_time_s is not None
        self._has_valid_device_sample = False
        self._connect_selected_source()

    def _abort_monitoring_after_reconnect_failure(self) -> None:
        if self._reconnect_timer.isActive():
            self._reconnect_timer.stop()
        self.workflow.suspend_acquisition()
        self._has_valid_device_sample = False
        self._sync_workflow_ui()
        self.manager.disconnect()
        self._record_debug_event(
            "reconnect_failed",
            {"attempts": self._reconnect_attempts, "source": self.selected_source},
        )
        self._stop_debug_logger("reconnect_failed")
        message = (
            f"采集源 {self.selected_source} 重连失败（已尝试 {self._reconnect_attempts} 次），"
            "本批次监测已中止，数据与入豆零点已保留；点击重新连接继续本锅。"
        )
        self.current_data_label.setText(message)
        self.statusBar().showMessage(message)
        self._set_session_status("警告：重连失败，监测已中止")

    def _replace_session_buffer(self, samples=None, events=None, actions=None) -> None:
        self.session_buffer = RoastSessionBuffer(
            samples=[restore_imported_sample(item) for item in (samples or [])],
            events=[dict(item) for item in (events or [])],
            actions=[dict(item) for item in (actions or [])],
        )
        self.samples = self.session_buffer.samples
        self.events = self.session_buffer.events
        self.actions = self.session_buffer.actions

    def _initial_action_snapshot(self) -> dict:
        gas_mbar = float(self.action_panel.gas_spin.value())
        return {
            "time_s": 0.0,
            "gas_mbar": gas_mbar,
            "gas_kpa": gas_mbar / 10.0,
            "damper_level": float(self.action_panel.damper_spin.value()),
            "rpm_level": float(self.action_panel.rpm_spin.value()),
            "control_scale": "0-10",
            "note": "锅次初始参数",
        }

    def _start_batch(self) -> None:
        self._history_source = "session"
        self._replace_session_buffer()
        if self.workflow.roast_active:
            self.actions.append(self._initial_action_snapshot())
        self._roast_finished = False
        self.quality_gate.reset()
        self.curve.set_data(self.samples, events=self.events)
        self.curve.set_targets(self.yellow_target_c, self.fc_target_c)
        self.curve.set_actions(self.actions)
        self.event_panel.set_events(self.events)
        self._refresh_development_metrics(0.0)
        self.metric_bt.value_widget.setText("--.-")
        self.metric_bt.sub_value_widget.setText("--")
        self.metric_et.value_widget.setText("--.-")
        self.metric_et.sub_value_widget.setText("--")
        self.metric_it.value_widget.setText("--.-")
        self.metric_it.sub_value_widget.setText("--")
        self.metric_exhaust.value_widget.setText("--.-")
        self.metric_exhaust.sub_value_widget.setText("--")
        self.metric_tp.value_widget.setText("--:--")
        self._update_forecast_chips()
        self._update_chart_pdf_availability()

    def _new_batch(self) -> None:
        if self.workflow.stage is RoastStage.BETWEEN:
            self._begin_monitoring()
            return
        if self.workflow.stage is not RoastStage.IDLE:
            self.statusBar().showMessage("请先结束当前烘焙锅次")
            return
        self._batch_metadata = {}
        self._start_batch()
        self.statusBar().showMessage("已清空未开始的内存锅次")
        self.current_data_label.setText("等待采集数据")

    def _open_batch(self) -> None:
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "打开烘焙档案",
            str(self._default_open_dir),
            "HB 烘焙档案 (*.hbroast)",
        )
        if not file_name:
            return
        self._clear_connection_retry()
        self._set_session_active(False)
        self.manager.disconnect()
        current_db = self.db
        try:
            next_db = RoastDatabase(file_name)
            history = next_db.list_history_sessions()
            if not history:
                next_db.close()
                QtWidgets.QMessageBox.information(self, "空档案", "所选文件中没有烘焙数据")
                return
            selected = history[0]
            roast_id = selected["id"]
            loaded = next_db.load_history_session(roast_id, source=selected["source"])
            if loaded is None:
                raise ValueError("所选烘焙记录不存在")
            next_samples = loaded["samples"]
            next_events = loaded["events"]
            next_actions = loaded["actions"]
        except Exception as exc:
            if "next_db" in locals():
                next_db.close()
            QtWidgets.QMessageBox.critical(self, "打开失败", f"无法打开文件：{exc}")
            return
        if current_db is not None:
            current_db.close()
        self.db = next_db
        self._default_open_dir = Path(file_name).resolve().parent
        self._batch_id = roast_id
        self._history_source = selected["source"]
        self._batch_metadata = dict(loaded["metadata"])
        self._batch_prepared_for_monitoring = False
        self._replace_session_buffer(
            next_samples,
            next_events,
            next_actions,
        )
        self._roast_finished = any(item.get("event_type") == "DROP" for item in self.events)
        self.event_panel.set_events(self.events)
        self.curve.set_actions(self.actions)
        self.curve.set_data(self.samples, events=self.events)
        self.curve.set_targets(self.yellow_target_c, self.fc_target_c)
        if self.samples:
            last = self.samples[-1]
            self._refresh_current_data(last)
        self._update_chart_pdf_availability()

    def _import_csv(self) -> None:
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入 Artisan / Cropster CSV", str(self._default_open_dir), "CSV 文件 (*.csv *.txt)"
        )
        if not file_name:
            return
        self._clear_connection_retry()
        self._set_session_active(False)
        self.manager.disconnect()
        try:
            batch_id = import_csv_batch(self.db, file_name)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导入失败", f"无法导入文件：{exc}")
            return
        self._batch_id = batch_id
        self._history_source = "legacy"
        self._batch_metadata = self.db.load_history_session(batch_id, source="legacy")["metadata"]
        self._batch_prepared_for_monitoring = False
        self._replace_session_buffer(
            self.db.load_samples(batch_id),
            self.db.load_events(batch_id),
            self.db.load_manual_actions(batch_id),
        )
        self._roast_finished = any(item.get("event_type") == "DROP" for item in self.events)
        self.event_panel.set_events(self.events)
        self.curve.set_actions(self.actions)
        self.curve.set_data(self.samples, events=self.events)
        self.curve.set_targets(self.yellow_target_c, self.fc_target_c)
        self._default_open_dir = Path(file_name).resolve().parent
        self.statusBar().showMessage(f"已导入 {len(self.samples)} 个采样点")
        self._update_chart_pdf_availability()

    def _connect_device(self) -> None:
        if self.session_active:
            QtWidgets.QMessageBox.information(self, "监测进行中", "请先结束监测，再切换采集源。")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择采集源与协议")
        layout = QtWidgets.QFormLayout(dialog)

        source_combo = QtWidgets.QComboBox()
        details = SerialManager.available_port_details()
        if not details:
            details = [{"device": item, "display": item} for item in SerialManager.available_ports()]
        for item in details:
            source_combo.addItem(item.get("display") or item["device"], item["device"])
        source_combo.addItem("SIMULATION（仅测试数据）", "SIMULATION")

        profile_combo = QtWidgets.QComboBox()
        profile_combo.addItem("HB Model S（M2S/M6S/L2S/L6S）", "HB_MODEL_S")
        profile_combo.addItem("HB_TC4（TC4 四路）", "HB_TC4")
        profile_combo.addItem("HB_LEGACY_3（旧三路）", "HB_LEGACY_3")
        baud_combo = QtWidgets.QComboBox()
        for baudrate in (115200, 9600, 19200):
            baud_combo.addItem(str(baudrate), baudrate)
        baud_combo.setCurrentIndex(0)
        help_label = QtWidgets.QLabel(
                "Model S 使用 CH34x USB 串口、115200 baud；程序按已验证的 1200 与 3400 两组通道读取四路温度。"
            "连接不会自动试探波特率；HB/TC4 仅在选定 profile 后发送 READ/CHAN。"
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("hintLabel")

        def update_controls() -> None:
            is_simulation = source_combo.currentData() == "SIMULATION"
            profile_combo.setEnabled(not is_simulation)
            baud_combo.setEnabled(not is_simulation)

        source_combo.currentIndexChanged.connect(update_controls)
        layout.addRow("采集源", source_combo)
        layout.addRow("协议 profile", profile_combo)
        layout.addRow("波特率", baud_combo)
        layout.addRow(help_label)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        update_controls()
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        self.selected_source = str(source_combo.currentData() or "")
        self.selected_source_display = str(source_combo.currentText() or "")
        self.selected_source_meta = {}
        for item in details:
            if str(item.get("device", "")) == self.selected_source:
                self.selected_source_meta = dict(item)
                break
        if self.selected_source == "SIMULATION":
            self.selected_profile = "SIMULATION"
            self.selected_baudrate = 0
            selected_text = "SIMULATION"
        else:
            self.selected_profile = str(profile_combo.currentData())
            self.selected_baudrate = int(baud_combo.currentData())
            selected_text = f"{self.selected_source} · {self.selected_profile} · {self.selected_baudrate} baud"
        self.statusBar().showMessage(f"已选择采集源：{selected_text}；点击开始监测后连接")
        self._set_session_status(f"待机（已选源：{selected_text}）")

    def _edit_batch_info(self) -> None:
        if self.workflow.stage is not RoastStage.IDLE:
            dialog = BatchInfoDialog(self, self._batch_metadata)
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                self._batch_metadata = dict(dialog.payload())
                self._update_chart_header()
                self.statusBar().showMessage("批次信息已暂存于当前内存锅次")
            return
        if not self._batch_id:
            QtWidgets.QMessageBox.warning(self, "批次", "当前没有活动批次")
            return
        source = self._history_source if self._history_source in ("archive", "legacy") else "legacy"
        record = self.db.load_history_session(self._batch_id, source=source)
        current = dict(record["metadata"]) if record else dict(self._batch_metadata)
        dialog = BatchInfoDialog(self, current)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            payload = dict(dialog.payload())
            batch_id = int(self._batch_id)
            snapshot = self._build_write_preview_snapshot(
                "保存批次",
                "✓ 确认保存批次信息",
                save_fields={"批次 ID": batch_id, **payload},
                after="确认后才会更新批次数据库记录；取消或关闭不会写入。",
            )

            def update_batch_info() -> None:
                if source == "archive":
                    self.db.update_history_metadata(batch_id, payload, source=source)
                else:
                    self.db.update_roast_info(
                        batch_id, payload["device"], payload["operator"], payload["coffee_name"],
                        payload["green_weight"], payload["roasted_weight"],
                    )
                self._batch_metadata.update(payload)
                self._update_chart_header()
                self.statusBar().showMessage("批次信息已保存")

            self._show_write_preview(snapshot, update_batch_info, "批次信息保存")

    def _save_batch_report(self) -> None:
        snapshot = self._build_write_preview_snapshot(
            "保存批次",
            "✓ 确认提交数据库事务",
            save_fields={
                "批次": self._batch_id or "当前内存锅次",
                "采样点": len(self.samples),
                "事件": len(self.events),
                "人工操作": len(self.actions),
                "写入目标": "当前数据库事务",
            },
            after="确认后才会提交当前数据库事务；取消或关闭不会提交。",
        )

        def commit_report() -> None:
            self.db.conn.commit()
            self.statusBar().showMessage("数据库事务已确认提交")

        self._show_write_preview(snapshot, commit_report, "数据库事务提交")

    def _export_data(self) -> None:
        if not self.samples:
            QtWidgets.QMessageBox.warning(self, "暂无数据", "当前批次还没有可导出的采样数据。")
            return

        if self._history_source in ("archive", "legacy") and self._batch_id is not None:
            record = self.db.load_history_session(self._batch_id, source=self._history_source)
            batch_info = dict(record["metadata"]) if record else dict(self._batch_metadata)
        else:
            batch_info = dict(self._batch_metadata)

        csv_target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 CSV",
            str(self._default_open_dir),
            "CSV (*.csv)",
        )
        json_target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 JSON",
            str(self._default_open_dir),
            "JSON (*.json)",
        )
        if not csv_target and not json_target:
            return
        samples = [dict(item) for item in self.samples]
        events = [dict(item) for item in self.events]
        actions = [dict(item) for item in self.actions]
        batch_info = dict(batch_info)
        report_text = build_report(batch_info, samples, events, actions)
        report_path = Path(csv_target or json_target).with_suffix(".report.txt")
        targets = [str(path) for path in (csv_target, json_target, str(report_path)) if path]
        snapshot = self._build_write_preview_snapshot(
            "导出曲线",
            "✓ 确认导出所选文件",
            export_summary={
                "文件": "\n".join(targets),
                "总行数": f"{len(samples)} 行",
                "格式": "CSV / JSON / Report",
                "文件数": len(targets),
            },
            export_rows=self._export_preview_rows(samples=samples, events=events, actions=actions),
            after="确认后才会创建所选导出文件；取消或关闭不会写入磁盘。",
        )

        def write_exports() -> None:
            writers = []
            if csv_target:
                writers.append((csv_target, lambda path: export_samples(samples, path)))
            if json_target:
                writers.append((json_target, lambda path: export_roast_json(batch_info, samples, events, actions, path)))
            writers.append((report_path, lambda path: path.write_text(report_text, encoding="utf-8")))
            export_atomic(writers)
            selected_target = json_target or csv_target
            self._default_open_dir = Path(selected_target).resolve().parent
            self.statusBar().showMessage(f"已导出 {len(targets)} 个文件")

        self._show_write_preview(snapshot, write_exports, "数据导出")

    def _on_sample_raw(self, raw: dict) -> None:
        if not self.workflow.acquisition_active:
            return
        raw = dict(raw)
        raw.setdefault("timestamp", QtCore.QDateTime.currentMSecsSinceEpoch() / 1000.0)
        try:
            device_time_s = float(raw["time_s"])
        except (KeyError, TypeError, ValueError, OverflowError):
            self.statusBar().showMessage("已忽略异常采样：采样时间无效")
            return
        if not math.isfinite(device_time_s) or device_time_s < 0:
            self.statusBar().showMessage("已忽略异常采样：采样时间必须是非负有限数值")
            return
        now = time.monotonic()
        offset = self._capture_offset_s
        gap = self._rebase_capture_on_next_sample
        if gap and self._last_capture_time_s is not None:
            offset = self._last_capture_time_s + max(0.001, now - self._last_capture_monotonic) - device_time_s
        capture_time_s = device_time_s + offset
        if self.workflow.roast_active and self._charge_time_origin_s is not None and capture_time_s < self._charge_time_origin_s:
            self.statusBar().showMessage("已忽略入豆前到达队列的采样")
            return
        if self._last_capture_time_s is not None and capture_time_s < self._last_capture_time_s:
            self.statusBar().showMessage("已忽略异常采样：设备时间回退")
            self._record_debug_event("sample_reject", {"reason": "capture_time_rollback", "raw": raw})
            return
        raw["device_time_s"] = device_time_s
        raw["capture_time_s"] = capture_time_s
        raw["time_s"] = capture_time_s
        mapped = self.mapper.map_sample(raw)
        if not mapped:
            if self._debug_logger:
                self._debug_logger.log_event(
                    "sample_reject",
                    {
                        "reason": "channel_mapping",
                        "raw": raw,
                        "detail": getattr(self.mapper, "last_error", ""),
                    },
                )
            self.statusBar().showMessage(
                f"已忽略采集帧：{getattr(self.mapper, 'last_error', '') or '温度通道无效'}"
            )
            return
        sample = dict(raw)
        sample.update(mapped)
        if gap:
            self.quality_gate.reset()
        decision = self.quality_gate.accept(sample)
        if not decision.accepted:
            if self._debug_logger:
                self._debug_logger.log_event(
                    "sample_reject",
                    {
                        "reason": decision.reason,
                        "raw": raw,
                    },
                )
            self.statusBar().showMessage(f"已忽略异常采样：{decision.reason}")
            return

        self._capture_offset_s = offset
        self._last_capture_time_s = capture_time_s
        self._last_capture_monotonic = now
        self._rebase_capture_on_next_sample = False
        if gap:
            self._live_samples.clear()
            sample["gap_before"] = True
            self._record_debug_event("acquisition_resumed", {"capture_time_s": capture_time_s})
        state = self.workflow.state.value
        if self._charge_time_origin_s is not None and state in ("roasting", "finished"):
            sample["time_s"] = max(0.0, capture_time_s - self._charge_time_origin_s)
        sample["elapsed_time"] = sample["time_s"] if state in ("roasting", "finished") else None
        sample["time_ms"] = int(sample["time_s"] * 1000)
        sample["channel_mapping"] = self.mapper.mapping_text
        self._live_samples.append(sample)
        while self._live_samples and self._live_samples[0]["time_s"] < sample["time_s"] - self.ror_window_seconds:
            self._live_samples.popleft()
        ror = calculate_ror(self._live_samples, window=self.ror_window_seconds)
        sample.update(ror)
        self._last_live_sample = sample
        if self._debug_logger:
            self._debug_logger.log_sample(sample, accepted=True)
        self._has_valid_device_sample = True
        self._apply_operation_flow_ui()
        if state in ("connected", "finished"):
            self._refresh_current_data(sample)
            return
        if state == "roasting" and self.actions:
            action = self.actions[-1]
            for key in ("gas_mbar", "gas_kpa", "damper_level", "rpm_level", "control_scale"):
                sample[key] = action.get(key)
        self.samples.append(sample)

        pred = predict_ror(self.samples, future_seconds=30)
        self.curve.set_data(self.samples, pred, self.events)

        if self.configuration_state.current.auto_tp_enabled and self.workflow.roast_active and not any(event.get("event_type") == "TP" for event in self.events):
            tp = detect_tp(self.samples)
            if tp is not None:
                self._record_tp(tp, "自动识别回温点")

        self._refresh_current_data(sample)

    def _update_menu_status_summary(self) -> None:
        if not hasattr(self, "menu_status_action"):
            return
        stage_text = (
            self.process_stage_badge.text()
            if hasattr(self, "process_stage_badge")
            else "待采集"
        )
        stats_text = (
            self.process_stats_label.text()
            if hasattr(self, "process_stats_label")
            else "采集总时长 00:00 · 已完成 0 锅"
        )
        self.menu_status_action.setText(f"{stage_text} · {stats_text}")

    def _configure_three_stage_flow(self) -> None:
        self._has_valid_device_sample = False
        self._charge_time_origin_s = None
        self._charge_monotonic = None
        self._roast_clock_timer = QtCore.QTimer(self)
        self._roast_clock_timer.setInterval(250)
        self._roast_clock_timer.timeout.connect(self._refresh_roast_clock)
        self._roast_clock_timer.start()
        self.start_monitor_button.setText("开始采集")
        self.start_monitor_button.setToolTip("建立一次设备通信连接并持续显示实时温度，不创建正式烘焙记录")
        self.start_monitor_button.setAccessibleDescription("设备采集控制，不启动正式烘焙计时")
        self.start_monitor_button.setStyleSheet(
            "QPushButton { background:#172b38; color:#73bfe8; border:1px solid #3e86aa; }"
            "QPushButton:disabled { color:#66727e; border-color:#39434d; background:#171c22; }"
        )
        self.start_next_batch_button.show()
        self.start_next_batch_button.setText("开始烘焙")
        self.start_next_batch_button.setToolTip("设备温度正常后建立新批次，进入等待入豆状态")
        self.start_next_batch_button.setAccessibleDescription("批次控制；入豆前只显示预热曲线，不计入正式记录")
        self.start_next_batch_button.setStyleSheet(
            "QPushButton { background:#f0821e; color:#111820; border:1px solid #ffad5c; font-weight:700; }"
            "QPushButton:disabled { color:#6f7378; border-color:#4a4037; background:#28231f; }"
        )
        self._apply_operation_flow_ui()

    def _apply_operation_flow_ui(self) -> None:
        if not hasattr(self, "start_monitor_button"):
            return
        state = getattr(getattr(self.workflow, "state", None), "value", "idle")
        has_valid_sample = bool(getattr(self, "_has_valid_device_sample", False))
        connected = state == "connected"
        ready = state == "ready_to_charge"
        roasting = state == "roasting"
        finished = state == "finished"
        suspended = self.workflow.acquisition_suspended
        self.start_monitor_button.setEnabled(state == "idle" or suspended)
        self.start_monitor_button.setText("重新连接" if suspended else ("开始采集" if state == "idle" else "设备已连接"))
        self.start_next_batch_button.show()
        self.start_next_batch_button.setText("开始烘焙")
        self.start_next_batch_button.setEnabled(connected and has_valid_sample and not suspended)
        self.start_next_batch_button.setToolTip(
            "采集已中断，请点击重新连接，恢复有效温度后再开始烘焙" if suspended
            else "等待有效设备温度；设备断开时请恢复连接，必要时停止采集后重新连接" if connected and not has_valid_sample
            else "本锅尚未成功保存，请在结束烘焙窗口确认保存或处理错误" if finished
            else "建立新锅次，等待入豆；入豆后正式计时和记录"
        )
        self.stop_monitor_button.setText("取消本次烘焙" if ready else "停止采集")
        self.stop_monitor_button.setEnabled(connected or ready)
        self.event_panel.set_session_active(ready or roasting)
        self.event_panel.set_ready_to_charge(ready and has_valid_sample and not suspended)
        self.action_panel.setEnabled(roasting)
        self.act_start.setEnabled(state == "idle" or suspended or (connected and has_valid_sample))
        self.act_start.setText("重新连接" if suspended else ("开始烘焙" if connected else "开始采集"))
        self.act_stop.setEnabled(connected or ready)
        self.act_stop.setText("取消本次烘焙" if ready else "停止采集")
        self.act_connect.setEnabled(state == "idle")
        for action in (self.act_open, self.act_import, self.act_channels):
            action.setEnabled(state == "idle")
        status_text = {
            "idle": "当前状态：尚未采集",
            "connected": "当前状态：设备已连接，温度数据正常" if has_valid_sample else "当前状态：设备已连接，等待温度反馈",
            "ready_to_charge": "当前状态：烘焙准备中，等待入豆",
            "roasting": "当前状态：烘焙进行中",
            "finished": "当前状态：已排豆，本锅正式记录已停止",
        }.get(state, "当前状态：未知")
        if suspended:
            status_text = "当前状态：采集中断，数据已保留，请重新连接"
        self.session_status_label.setText(status_text)
        self.process_stage_badge.setText(status_text.removeprefix("当前状态："))
        if ready:
            self.process_hint_label.setText("预热曲线仅供观察；点击入豆后计时从 00:00 开始并写入正式记录")
        elif finished:
            self.process_hint_label.setText("设备温度继续显示，排豆后的数据不会写入本锅正式记录")
        self._update_menu_status_summary()

    def _refresh_current_data(self, sample: dict) -> None:
        bt_ror_text = f"{sample.get('bt_ror'):.0f}" if sample.get("bt_ror") is not None else "-"
        it_ror_text = f"{sample.get('it_ror'):.0f}" if sample.get("it_ror") is not None else "-"
        et_ror_text = f"{sample.get('et_ror'):.0f}" if sample.get("et_ror") is not None else "-"
        it_text = f"{sample.get('it'):.1f}" if sample.get("it") is not None else "-"
        et_text = f"{sample.get('et'):.1f}" if sample.get("et") is not None else "-"
        bt_text = f"{sample.get('bt'):.1f}" if sample.get("bt") is not None else "-"
        exhaust_text = f"{sample.get('work'):.1f}" if sample.get("work") is not None else "-"
        exhaust_ror = sample.get("work_ror")
        exhaust_ror_text = f"{exhaust_ror:.0f}" if exhaust_ror is not None else "-"
        time_text = f"{sample.get('time_s'):.1f}" if sample.get("time_s") is not None else "-"
        self.current_data_label.setText(
            f"实时数据  IT {it_text} °C / IT RoR {it_ror_text} °C/min   "
            f"ET {et_text} °C / ET RoR {et_ror_text} °C/min   "
            f"BT {bt_text} °C / BT RoR {bt_ror_text} °C/min   "
            f"CH4 {exhaust_text} °C / CH4 RoR {exhaust_ror_text} °C/min   "
            f"· 已采集 {time_text} 秒   "
            f"· 来源 {sample.get('data_source') or sample.get('source') or 'UNKNOWN'}"
        )
        self.process_stats_label.setText(
            f"采集总时长 {self._preview_time(sample.get('time_s'))} · 已完成 {self._completed_charges} 锅"
        )
        self._update_menu_status_summary()
        self.metric_it.value_widget.setText(it_text)
        self.metric_it.sub_value_widget.setText(it_ror_text)
        self.metric_et.value_widget.setText(et_text)
        self.metric_et.sub_value_widget.setText(et_ror_text)
        self.metric_exhaust.value_widget.setText(exhaust_text)
        self.metric_exhaust.sub_value_widget.setText(exhaust_ror_text)
        state = self.workflow.state.value
        elapsed = float(sample.get("time_s") or 0.0)
        if state in ("connected", "ready_to_charge"):
            elapsed = 0.0
        elif state == "finished":
            elapsed = next((float(event["time_s"]) for event in self.events if event.get("event_type") == "DROP"), self._get_time_s())
        self.metric_time.value_widget.setText(f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
        tp_event = next((event for event in reversed(self.events) if event.get("event_type") == "TP"), None)
        tp_seconds = float(tp_event.get("time_s", 0.0)) if tp_event is not None else None
        self.metric_tp.value_widget.setText(f"{int(tp_seconds // 60):02d}:{int(tp_seconds % 60):02d}" if tp_seconds is not None else "--:--")
        self.metric_bt.value_widget.setText(bt_text)
        self.metric_bt.sub_value_widget.setText(bt_ror_text)
        forecast = predict_milestone_times(self.samples, self.yellow_target_c, self.fc_target_c)
        self.target_panel.set_forecast(forecast["yellow_eta_s"], forecast["fc_eta_s"])
        self._update_forecast_chips(forecast["yellow_eta_s"], forecast["fc_eta_s"])
        self.metric_ror.value_widget.setText(bt_ror_text)
        self._refresh_development_metrics(elapsed)

    @staticmethod
    def _format_eta_text(title: str, seconds: float | None) -> str:
        if seconds is None:
            return f"{title}：等待数据"
        if seconds < 0:
            return f"{title}：即将到达"
        return f"{title}：约 {int(seconds // 60):02d}:{int(seconds % 60):02d}"

    def _update_forecast_chips(self, yellow_eta: float | None = None, fc_eta: float | None = None) -> None:
        self.forecast_yellow_chip.setText(self._format_eta_text("预计转黄", yellow_eta))
        self.forecast_fc_chip.setText(self._format_eta_text("预计一爆", fc_eta))


    def _refresh_development_metrics(self, current_time_s: float | None = None) -> None:
        metrics = development_metrics(self.events, current_time_s if current_time_s is not None else self._get_time_s())
        duration = metrics["development_s"]
        ratio = metrics["development_ratio"]
        if duration is None:
            self.metric_dev_time.value_widget.setText("等待一爆")
            self.metric_dev_time.sub_value_widget.setText("--.- %")
            return
        self.metric_dev_time.value_widget.setText(f"{int(duration // 60):02d}:{int(duration % 60):02d}")
        suffix = "完成" if metrics["complete"] else "进行中"
        self.metric_dev_time.sub_value_widget.setText(f"{ratio:.1f}% {suffix}" if ratio is not None else "--.- %")

    def _open_replay(self) -> None:
        if not self.samples:
            QtWidgets.QMessageBox.information(self, "烘焙回放", "当前批次没有可回放的数据")
            return
        self._replay_window = ReplayWindow(self.samples, self.events, self.actions, self)
        self._replay_window.show()

    def _open_preview_center(self) -> None:
        snapshot = self._build_end_preview_snapshot()
        snapshot["mode"] = "preview"
        dialog = PreviewCenterDialog(snapshot, self)
        self._preview_dialog = dialog
        dialog.show()

    def _open_compare(self) -> None:
        if not self._batch_id:
            return
        if not self.db.list_profiles() and not self.db.list_history_sessions():
            QtWidgets.QMessageBox.information(self, "曲线对比", "尚无可对比的历史批次或参考曲线。")
            return
        source = self._history_source if self._history_source in ("archive", "legacy") else "auto"
        self._compare_window = CompareWindow(self._batch_id, self.db, self, source=source)
        self._compare_window.show()

    def _save_current_as_profile(self) -> None:
        if not self._batch_id or not self.samples:
            QtWidgets.QMessageBox.warning(self, "参考曲线", "当前批次没有采样数据")
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "保存参考曲线", "参考曲线名称："
        )
        if not ok or not name.strip():
            return
        notes, _ = QtWidgets.QInputDialog.getMultiLineText(
            self, "参考曲线备注", "备注"
        )
        export_file, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "同时导出参考曲线 JSON（可取消）",
            "",
            "JSON (*.json)",
        )
        batch_id = int(self._batch_id)
        profile_name = name.strip()
        profile_notes = notes.strip()
        snapshot = self._build_write_preview_snapshot(
            "保存批次",
            "✓ 确认保存参考曲线",
            save_fields={
                "批次 ID": batch_id,
                "参考曲线名称": profile_name,
                "备注": profile_notes or "无",
                "采样点": len(self.samples),
                "同时导出 JSON": export_file or "否",
            },
            after="确认后才会写入参考曲线数据库记录及可选 JSON；取消或关闭不会写入。",
        )

        def write_profile() -> None:
            source_args = {"source": "archive"} if self._history_source == "archive" else {}
            profile_id = save_profile_from_batch(
                db=self.db,
                batch_id=batch_id,
                name=profile_name,
                notes=profile_notes,
                **source_args,
            )
            if export_file:
                payload = self.db.load_profile_payload(profile_id) or "{}"
                export_profile_to_json(
                    {"id": profile_id, "payload": payload},
                    export_file,
                )
            self.statusBar().showMessage(f"参考曲线 #{profile_id} 已保存")

        self._show_write_preview(snapshot, write_profile, "参考曲线保存")

    def _detect_tp(self) -> None:
        result = detect_tp(self.samples)
        if result is None:
            QtWidgets.QMessageBox.information(self, "回温点", "尚未检测到回温点候选位置")
            return
        self._record_tp(result, "自动识别回温点")

    def _set_tp_manually(self) -> None:
        if not self.samples:
            QtWidgets.QMessageBox.information(self, "回温点", "当前没有采样数据")
            return
        time_s, ok = QtWidgets.QInputDialog.getDouble(
            self, "手动设定回温点", "回温点时间（秒）", self._get_time_s(), 0.0, 99999.0, 1
        )
        if not ok:
            return
        sample = min(self.samples, key=lambda item: abs(float(item.get("time_s", 0)) - time_s))
        self._record_tp(sample, "手动修正回温点")

    def _record_tp(self, sample: dict, note: str) -> None:
        time_s = float(sample.get("time_s", 0.0))
        previous = next((event for event in self.events if event.get("event_type") == "TP"), None)
        if previous is not None:
            if not self.workflow.roast_active:
                self.statusBar().showMessage("仅烘焙中可修正回温点")
                return
            self.events.remove(previous)
            self.workflow.recorded_event_types.discard("TP")
        try:
            self.workflow.record_event("TP", time_s, self.events)
        except WorkflowError as exc:
            if previous is not None:
                self.events.append(previous)
                self.events.sort(key=lambda item: float(item.get("time_s", 0)))
                self.workflow.recorded_event_types.add("TP")
            self.statusBar().showMessage(f"回温点未记录：{exc}")
            return
        event = {"event_type": "TP", "time_s": time_s, "it": sample.get("it"), "et": sample.get("et"), "bt": sample.get("bt"), "ror": sample.get("bt_ror"), "note": note}
        self.events.append(event)
        self.events.sort(key=lambda item: float(item.get("time_s", 0)))
        self.event_panel.set_events(self.events)
        self.curve.set_events(self.events)
        self._refresh_development_metrics()
        self.statusBar().showMessage(f"回温点已记录到内存：{time_s:.1f} 秒")

    def _show_analysis(self) -> None:
        findings = analyze_roast(self.samples, self.events, self.actions)
        QtWidgets.QMessageBox.information(self, "烘焙曲线分析", "\n\n".join(findings))

    def _edit_channel_mapping(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("温度通道映射")
        layout = QtWidgets.QFormLayout(dlg)
        ch1 = QtWidgets.QComboBox()
        ch2 = QtWidgets.QComboBox()
        ch3 = QtWidgets.QComboBox()
        ch4 = QtWidgets.QComboBox()
        for cb in (ch1, ch2, ch3):
            cb.addItems(["IT", "ET", "BT"])
        ch4.addItems(["WORK", "AMBIENT"])
        layout.addRow("通道 1", ch1)
        layout.addRow("通道 2", ch2)
        layout.addRow("通道 3", ch3)
        layout.addRow("通道 4", ch4)

        # load current
        ch1.setCurrentText(self.mapper.mapping.get("CH1", "IT"))
        ch2.setCurrentText(self.mapper.mapping.get("CH2", "ET"))
        ch3.setCurrentText(self.mapper.mapping.get("CH3", "BT"))
        ch4.setCurrentText(self.mapper.mapping.get("CH4", "WORK"))

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            mapping = {
                "CH1": ch1.currentText(),
                "CH2": ch2.currentText(),
                "CH3": ch3.currentText(),
                "CH4": ch4.currentText(),
            }
            try:
                self.mapper.set_mapping(mapping)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "通道映射无效", str(exc))
                return
            self.statusBar().showMessage("通道映射已更新")

    def _set_session_active(self, active: bool) -> None:
        active = bool(active)
        if active and self.workflow.stage is RoastStage.IDLE:
            before = self.workflow.stage.value
            self.workflow.start_acquisition()
            self._stage_log.append({"from": before, "to": self.workflow.stage.value})
        elif not active and self.workflow.stage is not RoastStage.IDLE:
            self.workflow.reset_acquisition()
        self._sync_workflow_ui()

    def _sync_workflow_ui(self) -> None:
        stage = self.workflow.stage
        acquisition_active = self.workflow.acquisition_active
        roasting = stage is RoastStage.ROASTING
        between = stage is RoastStage.BETWEEN
        self.session_active = acquisition_active
        self.event_panel.set_session_active(roasting)
        self.action_panel.setEnabled(roasting)
        self.start_monitor_button.setEnabled(stage in (RoastStage.IDLE, RoastStage.BETWEEN))
        if stage is RoastStage.IDLE:
            self.start_monitor_button.setText("▶ 开始采集")
        elif between:
            self.start_monitor_button.setText(f"▶ 开始下一锅 · #{self.workflow.batch_number + 1}")
        else:
            self.start_monitor_button.setText("● 采集中")
        self.start_next_batch_button.setEnabled(between)
        self.start_next_batch_button.setText(
            f"▶ 开始烘焙 · 第 {self.workflow.batch_number + 1} 锅" if between else "开始烘焙"
        )
        self.stop_monitor_button.setEnabled(between)
        self.stop_monitor_button.setText("■ 停止采集")
        self.reset_batch_button.setEnabled(self.workflow.can_finish)
        if stage is RoastStage.ROASTING:
            self.reset_batch_button.setText("■ 结束烘焙(强制)")
        elif stage is RoastStage.AWAIT_END:
            self.reset_batch_button.setText(f"✓ 结束烘焙 · 第 {self.workflow.batch_number} 锅")
        elif between:
            self.reset_batch_button.setText(f"已结束 #{self.workflow.batch_number}")
        else:
            self.reset_batch_button.setText("结束烘焙")
        self.undo_event_button.setEnabled(roasting and bool(self.events))
        self.act_start.setEnabled(stage in (RoastStage.IDLE, RoastStage.BETWEEN))
        self.act_start.setText("开始烘焙" if between else "开始采集")
        self.act_stop.setEnabled(between)
        self.act_stop.setText("停止采集")
        self.act_connect.setEnabled(not acquisition_active)
        self.act_new.setEnabled(stage in (RoastStage.IDLE, RoastStage.BETWEEN))
        self.act_open.setEnabled(not acquisition_active)
        self.act_import.setEnabled(not acquisition_active)
        self.act_save.setEnabled(not acquisition_active)
        self.act_export.setEnabled(not roasting)
        self.act_info.setEnabled(True)
        self.act_replay.setEnabled(bool(self.samples))
        self.act_profile.setEnabled(not acquisition_active)
        self.act_compare.setEnabled(not acquisition_active)
        self.act_tp.setEnabled(roasting)
        self.act_set_tp.setEnabled(roasting)
        self.act_analyze.setEnabled(bool(self.samples))
        self.act_channels.setEnabled(not acquisition_active)
        self.act_theme.setEnabled(not acquisition_active)
        self.act_background.setEnabled(not acquisition_active)
        self.act_clear_background.setEnabled(not acquisition_active)
        self.act_display.setEnabled(not acquisition_active)
        self._update_chart_pdf_availability()
        status = {
            RoastStage.IDLE: "待机（已连接）",
            RoastStage.ROASTING: f"烘焙中 · 第{self.workflow.batch_number}锅",
            RoastStage.AWAIT_END: f"待结束 · 第{self.workflow.batch_number}锅",
            RoastStage.BETWEEN: f"间隙 · 下一锅#{self.workflow.batch_number + 1}",
        }[stage]
        self._set_session_status(status)
        tone = {
            RoastStage.IDLE: "neutral",
            RoastStage.ROASTING: "active",
            RoastStage.AWAIT_END: "warning",
            RoastStage.BETWEEN: "success",
        }[stage]
        stage_text = {
            RoastStage.IDLE: "待采集",
            RoastStage.ROASTING: f"烘焙中 · 第{self.workflow.batch_number}锅",
            RoastStage.AWAIT_END: f"待结束 · 第{self.workflow.batch_number}锅",
            RoastStage.BETWEEN: f"间隙 · 下一锅#{self.workflow.batch_number + 1}",
        }[stage]
        guide = {
            RoastStage.IDLE: "未开始采集 · 点击「开始采集」将启动采集并自动开启第 1 锅",
            RoastStage.ROASTING: f"第 {self.workflow.batch_number} 锅烘焙中 · 请手动点击「投豆」标记下豆 · DROP 后进入「待结束」",
            RoastStage.AWAIT_END: f"第 {self.workflow.batch_number} 锅已 DROP · 点击「结束烘焙」先查看只读预览，确认后封存本锅",
            RoastStage.BETWEEN: f"第 {self.workflow.batch_number} 锅数据已独立保存 · 点击「开始烘焙」开启第 {self.workflow.batch_number + 1} 锅",
        }[stage]
        self.process_batch_label.setText("—" if stage is RoastStage.IDLE else f"第 {self.workflow.batch_number} 锅")
        self.process_stage_badge.setText(stage_text)
        self.process_stage_badge.setProperty("statusTone", tone)
        self.process_hint_label.setText(guide)
        self.process_stats_label.setText(
            f"采集总时长 {self._preview_time(self._get_time_s())} · 已完成 {self._completed_charges} 锅"
        )
        self._update_menu_status_summary()
        self.workflow_panel.set_state(
            stage.value,
            total_charges=self._completed_charges,
            current_charge=None if stage is RoastStage.IDLE else self.workflow.batch_number,
        )
        self._update_chart_header()
        self._apply_operation_flow_ui()
        for widget in (self.process_stage_badge, self.process_strip):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _begin_monitoring(self) -> None:
        if self.workflow.acquisition_suspended:
            self.workflow.resume_acquisition()
            self._has_valid_device_sample = False
            self._rebase_capture_on_next_sample = self._last_capture_time_s is not None
            self._clear_connection_retry()
            self._sync_workflow_ui()
            self._start_debug_logger()
            self._connect_selected_source()
            return
        if getattr(self.workflow.state, "value", "") == "connected":
            if not self._has_valid_device_sample:
                message = "请先开始采集并确认设备通信正常。"
                self.current_data_label.setText(message)
                self._set_session_status(message)
                self.statusBar().showMessage(message)
                return
            if self.workflow.stage is RoastStage.ROASTING:
                self.workflow.stage = RoastStage.BETWEEN
        if self.workflow.stage is RoastStage.BETWEEN:
            previous = self.workflow.stage.value
            self.workflow.start_next_batch()
            active_config = self.configuration_state.start_next_batch()
            self.ror_window_seconds = active_config.ror_window_seconds
            self._stage_log.append({"from": previous, "to": self.workflow.stage.value})
            self._batch_id = self.workflow.batch_number
            self._pending_archive_snapshot = None
            self._start_batch()
            self._sync_workflow_ui()
            self.current_data_label.setText(f"第 {self.workflow.batch_number} 锅已开始，采集持续运行")
            self.statusBar().showMessage(f"已开始第 {self.workflow.batch_number} 锅")
            return
        if self.workflow.stage is not RoastStage.IDLE:
            self.statusBar().showMessage("当前锅次尚未结束")
            return
        if not self.selected_source:
            message = "未选择采集源：请选择真实 COM 端口或显式选择 SIMULATION。"
            self.statusBar().showMessage(message)
            self.current_data_label.setText(message)
            self._set_session_status("警告：未选择采集源")
            return
        previous = self.workflow.stage.value
        self.workflow.start_acquisition()
        active_config = self.configuration_state.start_next_batch()
        self.ror_window_seconds = active_config.ror_window_seconds
        self._stage_log = [{"from": previous, "to": self.workflow.stage.value}]
        self._live_samples.clear()
        self._last_live_sample = None
        self._last_capture_time_s = None
        self._last_capture_monotonic = None
        self._capture_offset_s = 0.0
        self._charge_time_origin_s = None
        self._charge_monotonic = None
        self._rebase_capture_on_next_sample = False
        self._has_valid_device_sample = False
        self._batch_id = self.workflow.batch_number
        self._batch_metadata = {**self._batch_metadata, "device": self.selected_source}
        self._start_batch()
        self._roast_finished = False
        self._batch_prepared_for_monitoring = False
        self._clear_connection_retry()
        self._set_session_active(True)
        self._start_debug_logger()
        self._connect_selected_source()
        self.current_data_label.setText("监测已开始，等待温度数据")
        self._set_session_status(f"采集中 · {self.selected_source}")
        self.statusBar().showMessage(f"监测已开始：{self.selected_source}")

    def _end_monitoring(self) -> None:
        if getattr(self.workflow.state, "value", "") == "ready_to_charge":
            self.workflow.cancel_roast_preparation()
            self.samples.clear()
            self.events.clear()
            self._charge_time_origin_s = None
            self.event_panel.set_events([])
            self.curve.set_data(self.samples, None, self.events)
            self.curve.set_events(self.events)
            self._sync_workflow_ui()
            return
        if self.workflow.stage is RoastStage.IDLE:
            return
        try:
            self.workflow.stop_acquisition()
        except WorkflowError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._clear_connection_retry()
        self.manager.disconnect()
        self._stop_debug_logger("manual_stop")
        self._sync_workflow_ui()
        self.current_data_label.setText("监测已结束，温度采集已关闭")
        self._set_session_status("待机（已结束）")
        self._roast_finished = True
        self._update_chart_pdf_availability()
        self.statusBar().showMessage("监测已结束，事件与人工操作已锁定")

    @staticmethod
    def _preview_time(seconds: object) -> str:
        try:
            total = max(0, int(round(float(seconds or 0))))
        except (TypeError, ValueError):
            return "--"
        return f"{total // 60:02d}:{total % 60:02d}"

    def _export_preview_rows(
        self,
        *,
        samples: list[dict] | None = None,
        events: list[dict] | None = None,
        actions: list[dict] | None = None,
    ) -> list[dict]:
        buffer = RoastSessionBuffer(
            samples=[dict(item) for item in (self.samples if samples is None else samples)],
            events=[dict(item) for item in (self.events if events is None else events)],
            actions=[dict(item) for item in (self.actions if actions is None else actions)],
        )
        return [dict(item) for item in build_export_preview(self.workflow, buffer)["preview_rows"]]

    def _build_write_preview_snapshot(
        self,
        confirmation_tab: str,
        confirm_button_text: str,
        *,
        save_fields: dict | None = None,
        export_summary: dict | None = None,
        export_rows: list[dict] | None = None,
        after: str = "确认后才会执行写入；取消或关闭不会产生变更。",
    ) -> dict:
        batch_number = self._batch_id or self.workflow.batch_number or "--"
        return {
            "mode": "confirm",
            "initial_tab": confirmation_tab,
            "confirmation_tab": confirmation_tab,
            "confirm_button_text": confirm_button_text,
            "batch_number": batch_number,
            "end_roast": {"available": False},
            "export_curve": {
                "summary": dict(export_summary or {}),
                "rows": [dict(item) for item in (export_rows or [])],
                "after": after,
            },
            "save_batch": {
                "fields": dict(save_fields or {}),
                "after": after,
            },
            "replay": {"summary": {}, "events": []},
            "compare": {"available": False},
            "config_changes": {"rows": []},
        }

    def _show_write_preview(
        self,
        snapshot: dict,
        write_callback: Callable[[], None],
        operation_name: str,
    ) -> PreviewCenterDialog:
        dialog = PreviewCenterDialog(snapshot, self)
        executed = False
        cancelled = False

        def mark_cancelled() -> None:
            nonlocal cancelled
            cancelled = True

        def execute_once(_confirmed_snapshot: dict) -> None:
            nonlocal executed
            if executed or cancelled:
                return
            executed = True
            try:
                write_callback()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(
                    self,
                    f"{operation_name}失败",
                    f"无法完成{operation_name}：{exc}",
                )
                return
            dialog.accept()

        dialog.confirm_requested.connect(execute_once)
        dialog.rejected.connect(mark_cancelled)
        self._preview_dialog = dialog
        dialog.show()
        return dialog

    def _build_end_preview_snapshot(self) -> dict:
        end_preview = build_end_roast_preview(self.workflow, self.session_buffer, self._batch_metadata)
        export_preview = build_export_preview(self.workflow, self.session_buffer)
        summary = end_preview["summary"]
        sample_rows = end_preview["snapshot"]["samples"]
        event_rows = end_preview["snapshot"]["events"]
        duration_s = float(sample_rows[-1].get("time_s") or 0.0) if sample_rows else 0.0
        config_rows = [
            {
                "item": item["label"],
                "current": item["current"],
                "new": item["pending"],
            }
            for item in self.configuration_state.preview_data()
        ]
        return {
            "mode": "confirm",
            "initial_tab": "结束烘焙",
            "batch_number": self.workflow.batch_number,
            "end_roast": {
                "summary": {
                    "锅次": summary["batch"],
                    "本锅时长": summary["duration"],
                    "DROP 时刻": summary["drop_time"],
                    "事件": summary["events"],
                    "采样点": f"{summary['samples']} 点",
                    "曲线": f"{summary['curves']} 条",
                    "预计落库": f"~{summary['estimated_size_kb']:.1f} KB",
                    "写入目标": "charges 表",
                },
                "bt_curve": [(row.get("time_s", 0.0), row.get("bt")) for row in sample_rows if row.get("bt") is not None],
                "events": [
                    {
                        "event_type": row.get("event_type"),
                        "time": self._preview_time(row.get("time_s")),
                        "bt": row.get("bt"),
                    }
                    for row in event_rows
                ],
                "after": "确认后将原子写入 charges 与 batches；取消或关闭不会写入任何数据。",
            },
            "export_curve": {
                "summary": {
                    "文件": export_preview["file_name"],
                    "总行数": f"{len(export_preview['rows'])} 行",
                    "大小": f"~{summary['estimated_size_kb']:.1f} KB",
                    "列数": f"{len(SAMPLE_FIELDS)} 列（预览显示核心字段）",
                },
                "rows": export_preview["preview_rows"],
                "after": "此页仅预览，不会创建导出文件。",
            },
            "save_batch": {"fields": dict(self._batch_metadata)},
            "replay": {
                "summary": {
                    "回放对象": f"锅次 #{self.workflow.batch_number}",
                    "时长": self._preview_time(duration_s),
                    "数据点": f"{len(sample_rows)} 点",
                    "模式": "只读回放",
                },
                "duration_s": duration_s,
                "events": event_rows,
            },
            "compare": {"available": False},
            "config_changes": {"rows": config_rows},
        }

    def _request_finish_batch(self) -> None:
        if not self.workflow.can_finish:
            self.statusBar().showMessage("当前没有可结束的烘焙锅次")
            return
        if self._pending_archive_snapshot is not None and self._preview_dialog is not None and self._preview_dialog.isVisible():
            self._preview_dialog.raise_()
            self._preview_dialog.activateWindow()
            return
        self._pending_archive_snapshot = self.session_buffer.snapshot()
        dialog = PreviewCenterDialog(self._build_end_preview_snapshot(), self)
        dialog.archive_error_label = QtWidgets.QLabel(dialog)
        dialog.archive_error_label.setObjectName("archiveErrorLabel")
        dialog.archive_error_label.setAccessibleName("本锅保存状态")
        dialog.archive_error_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        dialog.archive_error_label.setWordWrap(True)
        dialog.archive_error_label.setStyleSheet("color: #e8544f; font-weight: 600; padding: 6px;")
        dialog.layout().insertWidget(dialog.layout().count() - 1, dialog.archive_error_label)
        dialog.archive_error_label.hide()
        dialog.discard_empty_button = QtWidgets.QPushButton("取消空锅，重新开始", dialog)
        dialog.discard_empty_button.clicked.connect(lambda: self._discard_empty_roast(dialog))
        dialog.confirm_button.parentWidget().layout().insertWidget(1, dialog.discard_empty_button)
        dialog.discard_empty_button.hide()
        dialog.confirm_requested.connect(self._confirm_finish_batch)
        dialog.rejected.connect(self._discard_pending_archive)
        self._preview_dialog = dialog
        if not self._pending_archive_snapshot["samples"]:
            self._show_archive_failure("本锅没有入豆后的有效采样，无法保存。可取消空锅后重新开始；不会补造温度数据。")
            dialog.confirm_button.setEnabled(False)
            dialog.discard_empty_button.show()
        dialog.show()

    def _show_archive_failure(self, message: str) -> None:
        self.statusBar().showMessage(message)
        dialog = self._preview_dialog
        if dialog is not None and hasattr(dialog, "archive_error_label"):
            dialog.archive_error_label.setText(message)
            dialog.archive_error_label.show()
            dialog.confirm_button.setText("重试保存本锅")

    def _discard_empty_roast(self, dialog) -> None:
        if dialog is not self._preview_dialog or not self.workflow.can_finish or self.samples:
            return
        choice = QtWidgets.QMessageBox.question(
            dialog, "确认取消空锅",
            "本锅没有正式采样。取消会清除本锅临时事件和人工操作，不生成烘焙档案。\n是否取消空锅并返回设备采集状态？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if choice != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.workflow.discard_empty_batch(len(self.samples))
        self.samples.clear()
        self.events.clear()
        self.actions.clear()
        self._pending_archive_snapshot = None
        self._charge_time_origin_s = None
        self._charge_monotonic = None
        self._roast_finished = False
        self.event_panel.set_events([])
        self.curve.set_data([], None, [])
        self.curve.set_events([])
        self._record_debug_event("empty_batch_discarded", {"saved": False})
        self._sync_workflow_ui()
        dialog.accept()
        self.statusBar().showMessage("空锅已取消，未生成档案；设备温度正常后可点击开始烘焙")

    def _discard_pending_archive(self) -> None:
        self._pending_archive_snapshot = None

    def _confirm_finish_batch(self, _preview_snapshot: dict) -> None:
        if not self.workflow.can_finish or self._pending_archive_snapshot is None:
            return
        pending = self._pending_archive_snapshot
        if not pending["samples"]:
            self._show_archive_failure("本锅没有正式采样，未保存。请取消空锅后重新开始。")
            return
        previous_stage = self.workflow.stage.value
        next_stage_log = [*self._stage_log, {"from": previous_stage, "to": RoastStage.BETWEEN.value}]
        metadata = {
            **self._batch_metadata,
            "batch_number": self.workflow.batch_number,
            "stage_log": next_stage_log,
        }
        try:
            result = self.db.archive_session(
                pending["samples"],
                pending["events"],
                pending["actions"],
                metadata,
            )
        except Exception as exc:
            detail = str(exc)
            reason = "数据库正被占用，请稍后重试" if any(word in detail.lower() for word in ("locked", "busy")) else detail
            self._show_archive_failure(f"保存失败，本锅尚未封存，数据仍保留。原因：{reason}。排除问题后点击“重试保存本锅”。")
            self._record_debug_event("archive_failed", {"error": detail, "batch_number": self.workflow.batch_number})
            return
        sample_rows = pending["samples"]
        event_rows = pending["events"]
        action_rows = pending["actions"]
        drop_event = next(
            (item for item in reversed(event_rows) if item.get("event_type") == "DROP"),
            {},
        )
        fc_start_event = next(
            (item for item in reversed(event_rows) if item.get("event_type") == "FC_START"),
            {},
        )
        duration_s = float(sample_rows[-1].get("time_s") or 0.0) if sample_rows else 0.0
        operation_count = max(0, len(action_rows) - 1)
        self._completed_charges += 1
        self.archive_summary_panel.set_summary(
            charge_number=result.charge_number,
            batch_number=metadata.get("batch_number"),
            coffee_name=metadata.get("coffee_name"),
            bean_origin=metadata.get("bean_origin") or metadata.get("origin"),
            charge_weight_g=metadata.get("charge_weight_g", metadata.get("green_weight")),
            duration_s=duration_s,
            drop_time_s=drop_event.get("time_s"),
            drop_bt=drop_event.get("bt"),
            fc_start_s=fc_start_event.get("time_s"),
            gas_action_count=operation_count,
            damper_action_count=operation_count,
            rpm_action_count=operation_count,
        )
        self.workflow.finish_current_batch()
        self._stage_log = next_stage_log
        self._last_archive_result = result
        self._batch_id = result.batch_id
        self._history_source = "archive"
        self._pending_archive_snapshot = None
        self._roast_finished = True
        self._sync_workflow_ui()
        if self._preview_dialog is not None:
            self._preview_dialog.accept()
        self.statusBar().showMessage(
            f"锅次 #{self.workflow.batch_number} 已封存保存 · 点击开始烘焙开启下一锅"
        )

    def _on_event_requested(self, event_type: str, time_s: float, note: str) -> None:
        current = self.samples[-1] if self.samples else (self._last_live_sample or {})
        current_it = current.get("it")
        current_et = current.get("et")
        current_bt = current.get("bt")
        current_ror = current.get("bt_ror")
        if (
            str(event_type).strip().upper() == "CHARGE"
            and getattr(self.workflow.state, "value", "") == "ready_to_charge"
        ):
            if not self._has_valid_device_sample or self.workflow.acquisition_suspended:
                self.statusBar().showMessage("请先恢复设备通信并确认温度正常，再入豆")
                return
            self._charge_time_origin_s = self._last_capture_time_s or 0.0
            self._charge_monotonic = time.monotonic()
            if self.selected_source != "SIMULATION" and self._last_capture_monotonic is not None:
                self._charge_time_origin_s += max(0.0, time.monotonic() - self._last_capture_monotonic)
            self._live_samples.clear()
            self.samples.clear()
            self.events.clear()
            self.actions[:] = [self._initial_action_snapshot()]
            self.event_panel.set_events([])
            self.curve.set_data(self.samples, None, self.events)
            self.curve.set_events(self.events)
            self.quality_gate.reset()
            time_s = 0.0
        try:
            self.workflow.record_event(event_type, time_s, self.events)
            event = add_event(self.events, event_type, time_s, current_bt, current_ror, note)
            event.update({"it": current_it, "et": current_et})
        except (ValueError, WorkflowError) as exc:
            self.event_panel.set_events(self.events)
            self.statusBar().showMessage(f"事件未记录：{exc}")
            return
        self.event_panel.set_events(self.events)
        self.curve.set_events(self.events)
        self._refresh_development_metrics()
        if event_type == "CHARGE":
            self._sync_workflow_ui()
            self.statusBar().showMessage("入豆已记录 · 正式烘焙计时从 00:00 开始")
        if event_type == "DROP":
            self._stage_log.append({"from": RoastStage.ROASTING.value, "to": RoastStage.AWAIT_END.value})
            self._sync_workflow_ui()
            self.statusBar().showMessage(
                f"DROP 出锅 · 第 {self.workflow.batch_number} 锅烘焙结束，点击结束烘焙先预览再执行"
            )
            self._update_chart_pdf_availability()

    def _on_event_cancelled(self, event_type: str) -> None:
        try:
            self.workflow.cancel_event(event_type)
        except WorkflowError as exc:
            self.event_panel.set_events(self.events)
            self.statusBar().showMessage(str(exc))
            return
        self.events = [item for item in self.events if item.get("event_type") != event_type]
        self.session_buffer.events = self.events
        self.event_panel.set_events(self.events)
        self.curve.set_events(self.events)
        self._refresh_development_metrics()
        self.statusBar().showMessage(f"已撤销 {event_type}")

    def _on_targets_changed(self, yellow_c: float, fc_c: float) -> None:
        self.yellow_target_c = yellow_c
        self.fc_target_c = fc_c
        self.curve.set_targets(yellow_c, fc_c)
        settings = QtCore.QSettings("HB", "RoastMonitor")
        settings.setValue("yellow_target_c", yellow_c)
        settings.setValue("fc_target_c", fc_c)
        if self.samples:
            forecast = predict_milestone_times(self.samples, yellow_c, fc_c)
            self.target_panel.set_forecast(forecast["yellow_eta_s"], forecast["fc_eta_s"])
            self._update_forecast_chips(forecast["yellow_eta_s"], forecast["fc_eta_s"])
            return
        self._update_forecast_chips()

    def _edit_display_settings(self) -> None:
        dialog = DisplaySettingsDialog(self.font_size, self.curve_scale, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.font_size, self.curve_scale = dialog.values()
        self._apply_theme(self.theme_name)
        self._apply_view_settings()
        self.statusBar().showMessage("显示设置已更新")

    def _apply_view_settings(self, save: bool = True) -> None:
        self.curve.apply_view_settings(self.font_size, self.curve_scale)
        if save:
            settings = QtCore.QSettings("HB", "RoastMonitor")
            settings.setValue("font_size", self.font_size)
            settings.setValue("curve_scale", self.curve_scale)

    def _load_background_curve(self) -> None:
        profiles = list_reference_profiles(self.db)
        choices = [f"{item['id']} | {item['name']}" for item in profiles]
        choices.append("从 JSON 文件加载")
        choice, ok = QtWidgets.QInputDialog.getItem(self, "加载背景曲线", "选择参考曲线", choices, 0, False)
        if not ok:
            return
        try:
            if choice == "从 JSON 文件加载":
                import json
                filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择参考曲线 JSON", str(self._default_open_dir), "JSON 文件 (*.json)")
                if not filename:
                    return
                payload = json.loads(Path(filename).read_text(encoding="utf-8"))
                if isinstance(payload, str):
                    payload = json.loads(payload)
                name = Path(filename).stem
            else:
                profile = load_reference_profile(self.db, int(choice.split(" | ", 1)[0]))
                payload = profile.get("payload", {})
                name = profile.get("name", "参考曲线")
            samples = payload.get("samples", []) if isinstance(payload, dict) else []
            if not samples:
                raise ValueError("参考曲线没有可用的采样数据")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "加载失败", f"无法加载背景曲线：{exc}")
            return
        self.curve.set_background_profile(samples, name)
        self.background_profile_name = str(name)
        self._update_chart_pdf_availability()
        self.statusBar().showMessage(f"已加载背景曲线：{name}")

    def _clear_background_curve(self) -> None:
        self.curve.set_background_profile([])
        self.background_profile_name = ""
        self._update_chart_pdf_availability()
        self.statusBar().showMessage("已清除背景曲线")

    def _on_action_added(self, gas: float, damper: float, rpm: float, note: str) -> None:
        if not self.workflow.roast_active:
            self.statusBar().showMessage("仅烘焙中可记录人工操作")
            return
        time_s = self._get_time_s()
        self.actions.append(
            {
                "time_s": time_s,
                "gas_mbar": gas * 10.0,
                "gas_kpa": gas,
                "damper_level": damper,
                "rpm_level": rpm,
                "control_scale": "0-10",
                "note": note,
            }
        )
        self.curve.set_actions(self.actions)
        self.statusBar().showMessage(
            f"已记录人工操作到内存：燃气 {gas * 10.0:.1f} mbar，风门 {damper:.1f} 档，转速 {rpm:.1f} 档"
        )

    def _on_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._set_session_status("警告：采集异常")
        self._set_power_status(False)
        if self._debug_logger:
            self._debug_logger.log_event(
                "serial_error",
                {
                    "message": message,
                    "source_display": self.selected_source_display,
                    "source": self.selected_source,
                    "profile": self.selected_profile,
                    "baudrate": self.selected_baudrate,
                },
            )
        if self.session_active and self.selected_source != "SIMULATION":
            self._schedule_reconnect()

    def _on_parse_error(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_parse_error_detail(self, reason: str, raw_frame: str, count: int) -> None:
        if self._debug_logger:
            self._debug_logger.log_parse_error(
                reason=reason,
                raw_frame=raw_frame,
                parse_error_count=count,
                source_display=self.selected_source_display,
                source=self.selected_source,
                profile=self.selected_profile,
                baudrate=self.selected_baudrate,
            )

    def _on_state_changed(self, connected: bool) -> None:
        self.statusBar().showMessage("采集设备已连接" if connected else "采集设备已断开")
        self._set_power_status(connected)
        if connected:
            if self._debug_logger:
                self._debug_logger.log_event(
                    "serial_state",
                    {
                        "connected": True,
                        "source": self.selected_source,
                        "source_display": self.selected_source_display,
                    },
                )
            self._clear_connection_retry()
            if self.session_active:
                self._set_session_status("采集中（设备已连接）")
            else:
                self._set_session_status("设备已连接")
        else:
            self._has_valid_device_sample = False
            self._apply_operation_flow_ui()
            if self._debug_logger:
                self._debug_logger.log_event(
                    "serial_state",
                    {
                        "connected": False,
                        "source": self.selected_source,
                        "source_display": self.selected_source_display,
                    },
                )
            if not self.session_active:
                self._set_session_status("待机（设备未连接）")
                return
            self._set_session_status("采集中（设备断开）")
            if self.selected_source != "SIMULATION":
                self._schedule_reconnect()

    def _set_session_status(self, text: str) -> None:
        if hasattr(self, "session_status_label"):
            self.session_status_label.setText(f"状态：{text}")

    def _get_time_s(self) -> float:
        if getattr(self.workflow.state, "value", "") == "ready_to_charge":
            return 0.0
        if self.workflow.roast_active and self.selected_source != "SIMULATION" and self._charge_monotonic is not None:
            return max(0.0, time.monotonic() - self._charge_monotonic)
        if self.workflow.state.value == "finished":
            drop = next((event for event in self.events if event.get("event_type") == "DROP"), None)
            if drop is not None:
                return float(drop["time_s"])
        if self.samples:
            return float(self.samples[-1]["time_s"])
        return 0.0

    def _refresh_roast_clock(self) -> None:
        if self.workflow.roast_active:
            elapsed = self._get_time_s()
            self.metric_time.value_widget.setText(f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
            self._refresh_development_metrics(elapsed)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._roast_clock_timer.stop()
        self._clear_connection_retry()
        self._set_session_active(False)
        self._stop_debug_logger("app_closed")
        self.manager.disconnect()
        self.db.close()
        super().closeEvent(event)
