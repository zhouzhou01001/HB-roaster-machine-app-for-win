"""Side-effect-free editor and preview dialog for roast configuration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from PySide6 import QtCore, QtWidgets

from app.roast.configuration import (
    HardwareProtocol,
    RoastConfiguration,
    RoastConfigurationState,
    Theme,
)


_PROTOCOL_OPTIONS = (
    ("HB Model S", HardwareProtocol.HB_MODEL_S),
    ("HB TC4", HardwareProtocol.HB_TC4),
    ("HB Legacy 3", HardwareProtocol.HB_LEGACY_3),
    ("模拟", HardwareProtocol.SIMULATION),
)
_THEME_OPTIONS = (("深色", Theme.DARK), ("浅色", Theme.LIGHT))
_TIMING_LABELS = {
    "immediate": "确认后",
    "next_batch": "下一锅",
    "next_cleanup": "下次清理",
    "on_reconnect": "重新连接",
}


class RoastConfigurationDialog(QtWidgets.QDialog):
    """Edit a detached candidate, preview its diff, and emit confirmation intent."""

    confirm_requested = QtCore.Signal(dict)

    def __init__(self, state: RoastConfigurationState, parent=None) -> None:
        if not isinstance(state, RoastConfigurationState):
            raise TypeError("state must be a RoastConfigurationState")
        super().__init__(parent)
        self._state = state
        self._preview_payload: dict | None = None
        initial = state.pending or state.current

        self.setObjectName("roastConfigurationDialog")
        self.setProperty("uiRole", "dialog")
        self.setWindowTitle("烘焙配置")
        self.setModal(True)
        self.resize(780, 680)
        self.setMinimumSize(620, 500)
        self.setSizeGripEnabled(True)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        notice = QtWidgets.QLabel("所有编辑仅在本窗口生成预览；确认后由宿主安排下一锅生效。")
        notice.setObjectName("configurationSafetyNotice")
        notice.setProperty("uiRole", "hintPanel")
        notice.setWordWrap(True)
        notice.setAccessibleName("配置安全说明")
        root.addWidget(notice)

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("configurationScroll")
        scroll.setProperty("uiRole", "contentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        content = QtWidgets.QWidget()
        content.setObjectName("configurationContent")
        content.setProperty("uiRole", "tabContent")
        self._form_grid = QtWidgets.QGridLayout(content)
        self._form_grid.setContentsMargins(4, 4, 4, 4)
        self._form_grid.setHorizontalSpacing(8)
        self._form_grid.setVerticalSpacing(8)
        self._form_sections: list[QtWidgets.QGroupBox] = []

        self.sample_rate_combo = QtWidgets.QComboBox()
        self.sample_rate_combo.setObjectName("sampleRateCombo")
        self.sample_rate_combo.setProperty("uiRole", "formField")
        for rate in (1, 2, 4, 5, 10):
            self.sample_rate_combo.addItem(f"{rate} Hz", rate)
        self.sample_rate_combo.setCurrentIndex(self.sample_rate_combo.findData(initial.sample_rate_hz))
        self.sample_rate_combo.setAccessibleName("采集频率")
        self.sample_rate_combo.setAccessibleDescription("允许选择 1、2、4、5 或 10 Hz，下一锅生效")
        acquisition_section = self._section(
            "acquisitionSection", "采集频率", "下一锅生效", self.sample_rate_combo
        )
        self._form_sections.append(acquisition_section)
        self._form_grid.addWidget(acquisition_section, 0, 0)

        self.ror_window_spin = QtWidgets.QSpinBox()
        self.ror_window_spin.setObjectName("rorWindowSpin")
        self.ror_window_spin.setProperty("uiRole", "numericField")
        self.ror_window_spin.setRange(1, 60)
        self.ror_window_spin.setSuffix(" 秒")
        self.ror_window_spin.setValue(initial.ror_window_seconds)
        self.ror_window_spin.setAccessibleName("RoR 平滑窗口")
        ror_section = self._section("rorSection", "RoR 平滑窗口", "1–60 秒", self.ror_window_spin)
        self._form_sections.append(ror_section)
        self._form_grid.addWidget(ror_section, 0, 1)

        self.auto_tp_checkbox = QtWidgets.QCheckBox("启用自动识别回温点")
        self.auto_tp_checkbox.setObjectName("autoTpCheckBox")
        self.auto_tp_checkbox.setProperty("uiRole", "formCheck")
        self.auto_tp_checkbox.setChecked(initial.auto_tp_enabled)
        self.auto_tp_checkbox.setAccessibleName("自动 TP")
        self.auto_tp_checkbox.setAccessibleDescription("默认关闭；勾选后纳入待确认配置")
        auto_tp_section = self._section("autoTpSection", "自动 TP", "默认关闭", self.auto_tp_checkbox)
        self._form_sections.append(auto_tp_section)
        self._form_grid.addWidget(auto_tp_section, 1, 0)

        self.hardware_protocol_combo = QtWidgets.QComboBox()
        self.hardware_protocol_combo.setObjectName("hardwareProtocolCombo")
        self.hardware_protocol_combo.setProperty("uiRole", "formField")
        for label, protocol in _PROTOCOL_OPTIONS:
            self.hardware_protocol_combo.addItem(label, protocol)
        self.hardware_protocol_combo.setCurrentIndex(
            self.hardware_protocol_combo.findData(initial.hardware_protocol)
        )
        self.hardware_protocol_combo.setAccessibleName("硬件协议")
        self.hardware_protocol_combo.setAccessibleDescription("选择协议不会连接或操作设备")
        protocol_section = self._section(
            "protocolSection", "硬件协议", "重连后生效", self.hardware_protocol_combo
        )
        self._form_sections.append(protocol_section)
        self._form_grid.addWidget(protocol_section, 1, 1)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.setObjectName("themeCombo")
        self.theme_combo.setProperty("uiRole", "formField")
        for label, theme in _THEME_OPTIONS:
            self.theme_combo.addItem(label, theme)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(initial.theme))
        self.theme_combo.setAccessibleName("界面主题")
        theme_section = self._section("themeSection", "界面主题", "深色 / 浅色", self.theme_combo)
        self._form_sections.append(theme_section)
        self._form_grid.addWidget(theme_section, 2, 0)

        self.retention_spin = QtWidgets.QSpinBox()
        self.retention_spin.setObjectName("dataRetentionSpin")
        self.retention_spin.setProperty("uiRole", "numericField")
        self.retention_spin.setRange(30, 365)
        self.retention_spin.setSuffix(" 天")
        self.retention_spin.setValue(initial.data_retention_days)
        self.retention_spin.setAccessibleName("数据保留期限")
        retention_section = self._section("retentionSection", "数据保留", "30–365 天", self.retention_spin)
        self._form_sections.append(retention_section)
        self._form_grid.addWidget(retention_section, 2, 1)

        self._form_grid.setColumnStretch(0, 1)
        self._form_grid.setColumnStretch(1, 1)
        self.preview_table = self._build_preview_table()
        self.preview_panel = QtWidgets.QGroupBox("更改预览")
        self.preview_panel.setObjectName("configurationPreviewPanel")
        self.preview_panel.setProperty("uiRole", "panelSecondary")
        preview_layout = QtWidgets.QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(12, 16, 12, 12)
        self.preview_status_label = QtWidgets.QLabel("编辑完成后点击“预览更改”。")
        self.preview_status_label.setObjectName("configurationPreviewStatus")
        self.preview_status_label.setProperty("uiRole", "secondaryText")
        self.preview_status_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_status_label)
        preview_layout.addWidget(self.preview_table)
        self._form_grid.addWidget(self.preview_panel, 3, 0, 1, 2)
        self._form_grid.setRowStretch(3, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        root.addWidget(self._build_actions())
        self._connect_edit_signals()
        self._set_tab_order()
        self._apply_responsive_layout(self.width())

    def _build_header(self) -> QtWidgets.QHBoxLayout:
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("烘焙配置")
        title.setObjectName("dialogTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("先核对差异，再确认下一锅配置")
        subtitle.setObjectName("configurationSubtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        badge = QtWidgets.QLabel("待确认 · 未持久化")
        badge.setObjectName("configurationStatusBadge")
        badge.setProperty("uiRole", "statusBadge")
        badge.setProperty("statusTone", "neutral")
        badge.setAccessibleName("配置状态：待确认，未持久化")
        header.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        return header

    def _section(
        self,
        object_name: str,
        title: str,
        detail: str,
        control: QtWidgets.QWidget,
    ) -> QtWidgets.QGroupBox:
        section = QtWidgets.QGroupBox(title)
        section.setObjectName(object_name)
        section.setProperty("uiRole", "formPanel")
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        detail_label = QtWidgets.QLabel(detail)
        detail_label.setObjectName(f"{object_name}Detail")
        detail_label.setProperty("uiRole", "secondaryText")
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)
        layout.addWidget(control)
        layout.addStretch()
        return section

    def _build_preview_table(self) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 4)
        table.setObjectName("configurationDiffTable")
        table.setProperty("uiRole", "previewTable")
        table.setHorizontalHeaderLabels(("配置项", "当前", "待应用", "生效时机"))
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(150)
        table.setAccessibleName("配置更改预览")
        return table

    def _build_actions(self) -> QtWidgets.QFrame:
        action_host = QtWidgets.QFrame()
        action_host.setObjectName("configurationActions")
        action_host.setProperty("uiRole", "dialogActions")
        actions = QtWidgets.QHBoxLayout(action_host)
        actions.setContentsMargins(0, 8, 0, 0)
        actions.setSpacing(8)
        self.action_note = QtWidgets.QLabel("确认只发送配置意图，不写入文件、数据库或设备。")
        self.action_note.setObjectName("configurationActionNote")
        self.action_note.setProperty("uiRole", "secondaryText")
        self.action_note.setWordWrap(True)
        actions.addWidget(self.action_note, 1)
        self.cancel_button = QtWidgets.QPushButton("取消")
        self.cancel_button.setObjectName("configurationCancelButton")
        self.cancel_button.setProperty("uiRole", "quietButton")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.preview_button = QtWidgets.QPushButton("预览更改")
        self.preview_button.setObjectName("configurationPreviewButton")
        self.preview_button.setProperty("uiRole", "secondaryButton")
        self.preview_button.clicked.connect(self._preview_changes)
        actions.addWidget(self.preview_button)
        self.confirm_button = QtWidgets.QPushButton("确认 · 下一锅生效")
        self.confirm_button.setObjectName("configurationConfirmButton")
        self.confirm_button.setProperty("uiRole", "primaryButton")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._emit_confirmation)
        actions.addWidget(self.confirm_button)
        return action_host

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_form_grid"):
            self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        stacked = width < 700
        self.setProperty("layoutMode", "stacked" if stacked else "grid")
        if stacked:
            for row, section in enumerate(self._form_sections):
                self._form_grid.addWidget(section, row, 0, 1, 2)
            self._form_grid.addWidget(self.preview_panel, len(self._form_sections), 0, 1, 2)
        else:
            for index, section in enumerate(self._form_sections):
                self._form_grid.addWidget(section, index // 2, index % 2)
            self._form_grid.addWidget(self.preview_panel, 3, 0, 1, 2)

    def _connect_edit_signals(self) -> None:
        self.sample_rate_combo.currentIndexChanged.connect(self._invalidate_preview)
        self.ror_window_spin.valueChanged.connect(self._invalidate_preview)
        self.auto_tp_checkbox.toggled.connect(self._invalidate_preview)
        self.hardware_protocol_combo.currentIndexChanged.connect(self._invalidate_preview)
        self.theme_combo.currentIndexChanged.connect(self._invalidate_preview)
        self.retention_spin.valueChanged.connect(self._invalidate_preview)

    def _set_tab_order(self) -> None:
        controls = (
            self.sample_rate_combo,
            self.ror_window_spin,
            self.auto_tp_checkbox,
            self.hardware_protocol_combo,
            self.theme_combo,
            self.retention_spin,
            self.cancel_button,
            self.preview_button,
            self.confirm_button,
        )
        for current, following in zip(controls, controls[1:]):
            QtWidgets.QWidget.setTabOrder(current, following)

    def candidate_configuration(self) -> RoastConfiguration:
        """Build a validated detached snapshot from the controls."""

        return RoastConfiguration(
            sample_rate_hz=int(self.sample_rate_combo.currentData()),
            ror_window_seconds=self.ror_window_spin.value(),
            theme=self.theme_combo.currentData(),
            auto_tp_enabled=self.auto_tp_checkbox.isChecked(),
            data_retention_days=self.retention_spin.value(),
            hardware_protocol=self.hardware_protocol_combo.currentData(),
        )

    def preview_payload(self) -> dict | None:
        """Return a deep copy of the last valid preview payload."""

        return deepcopy(self._preview_payload)

    @QtCore.Slot()
    def _invalidate_preview(self, *_args) -> None:
        if self._preview_payload is None:
            return
        self._preview_payload = None
        self.preview_table.setRowCount(0)
        self.preview_status_label.setText("配置已继续编辑，请重新预览更改。")
        self.confirm_button.setEnabled(False)

    @QtCore.Slot()
    def _preview_changes(self) -> None:
        candidate = self.candidate_configuration()
        detached_state = RoastConfigurationState(self._state.current)
        detached_state.stage(**asdict(candidate))
        diff = detached_state.preview_data()
        configuration = {
            "sample_rate_hz": candidate.sample_rate_hz,
            "ror_window_seconds": candidate.ror_window_seconds,
            "theme": candidate.theme.value,
            "auto_tp_enabled": candidate.auto_tp_enabled,
            "data_retention_days": candidate.data_retention_days,
            "hardware_protocol": candidate.hardware_protocol.value,
        }
        self._preview_payload = {
            "configuration": configuration,
            "diff": [dict(item) for item in diff],
            "apply_at": "next_batch",
        }
        self._populate_preview(diff)
        if diff:
            self.preview_status_label.setText(f"已生成 {len(diff)} 项更改；确认后交由宿主在下一锅应用。")
            self.confirm_button.setEnabled(True)
        else:
            self.preview_status_label.setText("当前填写内容与已生效配置一致，无需应用。")
            self.confirm_button.setEnabled(False)

    def _populate_preview(self, diff: tuple[dict[str, int | bool | str], ...]) -> None:
        self.preview_table.setRowCount(len(diff))
        for row, item in enumerate(diff):
            values = (
                item["label"],
                self._display_value(item["current"]),
                self._display_value(item["pending"]),
                _TIMING_LABELS.get(str(item["effective_timing"]), str(item["effective_timing"])),
            )
            for column, value in enumerate(values):
                table_item = QtWidgets.QTableWidgetItem(str(value))
                table_item.setFlags(table_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.preview_table.setItem(row, column, table_item)
        self.preview_table.resizeRowsToContents()

    @staticmethod
    def _display_value(value: int | bool | str) -> str:
        if type(value) is bool:
            return "开启" if value else "关闭"
        protocol_labels = {protocol.value: label for label, protocol in _PROTOCOL_OPTIONS}
        theme_labels = {theme.value: label for label, theme in _THEME_OPTIONS}
        return protocol_labels.get(str(value), theme_labels.get(str(value), str(value)))

    @QtCore.Slot()
    def _emit_confirmation(self) -> None:
        if self._preview_payload is None or not self.confirm_button.isEnabled():
            return
        self.confirm_requested.emit(deepcopy(self._preview_payload))
        self.accept()


__all__ = ["RoastConfigurationDialog"]
