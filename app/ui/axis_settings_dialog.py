from __future__ import annotations

from typing import Mapping

from PySide6 import QtCore, QtWidgets


class AxisSettingsDialog(QtWidgets.QDialog):
    """Professional three-axis console for the live roasting scope."""

    AXES = (
        ("x", "时间轴", "X · ROAST TIME", "s", "#4fb3ff"),
        ("left", "温度轴", "LEFT · TEMP / CONTROL", "°C", "#ff8c2f"),
        ("right", "升温率轴", "RIGHT · RoR", "°C/min", "#35c98e"),
    )
    PRESETS = {
        "x": (("10 分钟", 0.0, 600.0, 60.0), ("15 分钟", 0.0, 900.0, 60.0), ("20 分钟", 0.0, 1200.0, 120.0)),
        "left": (("全量", 0.0, 300.0, 50.0), ("烘焙", 50.0, 250.0, 25.0), ("精细", 80.0, 240.0, 20.0)),
        "right": (("标准", -10.0, 20.0, 5.0), ("精细", -5.0, 15.0, 2.5), ("正向", 0.0, 20.0, 2.0)),
    }
    DEFAULTS = {
        "x": {"min": 0.0, "max": 600.0, "major": 60.0},
        "left": {"min": 0.0, "max": 300.0, "major": 50.0},
        "right": {"min": -10.0, "max": 20.0, "major": 5.0},
    }

    def __init__(self, settings: Mapping[str, Mapping], auto_states: Mapping[str, bool], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("axisSettingsDialog")
        self.setProperty("uiRole", "dialog")
        self.setWindowTitle("曲线坐标控制台")
        self.resize(900, 520)
        self.setMinimumSize(760, 480)
        self.setSizeGripEnabled(True)
        self._updating = False
        self.controls: dict[str, dict] = {}

        dark = getattr(parent, "theme_name", "深色工作台") != "浅色工作台"
        bg = "#14171c" if dark else "#f3f5f8"
        panel = "#1c212a" if dark else "#ffffff"
        panel2 = "#242b38" if dark else "#eef1f6"
        line = "#2c3442" if dark else "#d8dee8"
        text = "#e8ecf2" if dark else "#20242c"
        dim = "#8a94a6" if dark else "#66707f"
        self.setStyleSheet(f"""
            QDialog#axisSettingsDialog {{ background: {bg}; color: {text}; }}
            QDialog#axisSettingsDialog QLabel {{ color: {text}; }}
            QFrame#axisCard {{ background: {panel}; border: 1px solid {line}; border-radius: 8px; }}
            QLabel#axisCode {{ color: {dim}; font-size: 10px; letter-spacing: 1px; }}
            QLabel#axisUnitBadge {{ color: {dim}; background: {panel2}; border: 1px solid {line}; border-radius: 8px; padding: 2px 7px; }}
            QLabel#axisStatus {{ color: {dim}; background: {panel}; border: 1px solid {line}; border-radius: 6px; padding: 7px 10px; }}
            QLabel#axisStatus[valid="false"] {{ color: #e8544f; border-color: #e8544f; }}
            QToolButton#axisPresetButton {{ min-height: 22px; padding: 2px 7px; font-weight: 500; }}
            QToolButton#axisUtilityButton {{ min-height: 24px; padding: 3px 9px; }}
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("曲线坐标控制台")
        title.setObjectName("dialogTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("分别控制时间、温度与 RoR；拖动曲线会自动切换为固定范围")
        subtitle.setObjectName("subtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack, 1)
        self.auto_all_button = QtWidgets.QToolButton()
        self.auto_all_button.setObjectName("axisUtilityButton")
        self.auto_all_button.setText("全部自动")
        self.auto_all_button.setToolTip("三个坐标轴均随实时数据自动适应")
        self.reset_button = QtWidgets.QToolButton()
        self.reset_button.setObjectName("axisUtilityButton")
        self.reset_button.setText("恢复建议值")
        header.addWidget(self.auto_all_button)
        header.addWidget(self.reset_button)
        root.addLayout(header)

        hint = QtWidgets.QLabel("自动模式保留主刻度设置；固定模式可编辑最小值、最大值和跨度。跨度始终以最小值为起点更新最大值。")
        hint.setObjectName("hintPanel")
        hint.setProperty("uiRole", "hintPanel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(10)
        tab_controls: list[QtWidgets.QWidget] = []
        for key, axis_title, code, unit, color in self.AXES:
            values = dict(settings[key])
            card = QtWidgets.QFrame()
            card.setObjectName("axisCard")
            card.setProperty("uiRole", "formPanel")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(12, 12, 12, 12)
            card_layout.setSpacing(8)

            card_header = QtWidgets.QHBoxLayout()
            accent = QtWidgets.QFrame()
            accent.setFixedSize(4, 34)
            accent.setStyleSheet(f"background: {color}; border-radius: 2px;")
            labels = QtWidgets.QVBoxLayout()
            labels.setSpacing(0)
            axis_label = QtWidgets.QLabel(axis_title)
            axis_label.setProperty("uiRole", "sectionTitle")
            axis_code = QtWidgets.QLabel(code)
            axis_code.setObjectName("axisCode")
            labels.addWidget(axis_label)
            labels.addWidget(axis_code)
            auto = QtWidgets.QCheckBox("自动")
            auto.setObjectName(f"axis_{key}_auto_check")
            auto.setChecked(bool(auto_states.get(key, False)))
            auto.setToolTip("随实时数据更新自动计算显示范围")
            card_header.addWidget(accent)
            card_header.addLayout(labels, 1)
            card_header.addWidget(auto, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
            card_layout.addLayout(card_header)

            unit_badge = QtWidgets.QLabel(f"单位 {unit}")
            unit_badge.setObjectName("axisUnitBadge")
            unit_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(unit_badge)

            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(5)
            controls: dict[str, object] = {"auto": auto}
            if auto.isChecked() and float(values["max"]) - float(values["min"]) < float(values["major"]):
                values["max"] = float(values["min"]) + float(values["major"])
                values["span"] = float(values["major"])
            fields = (("min", "最小值"), ("max", "最大值"), ("span", "显示跨度"), ("major", "主刻度"))
            for index, (field, label_text) in enumerate(fields):
                row = (index // 2) * 2
                column = index % 2
                label = QtWidgets.QLabel(label_text)
                label.setProperty("uiRole", "fieldLabel")
                spin = QtWidgets.QDoubleSpinBox()
                spin.setObjectName(f"axis_{key}_{field}_spin")
                spin.setProperty("uiRole", "numericField")
                lower = 0.0 if key == "x" or field in ("span", "major") else -10000.0
                upper = 99999.0 if key == "x" else 10000.0
                if field in ("span", "major"):
                    lower = 0.1
                spin.setRange(lower, upper)
                spin.setDecimals(1 if key != "right" else 2)
                spin.setSingleStep(10.0 if key == "x" else 5.0 if key == "left" else 1.0)
                spin.setSuffix(f" {unit}")
                spin.setValue(float(values[field]))
                label.setBuddy(spin)
                grid.addWidget(label, row, column)
                grid.addWidget(spin, row + 1, column)
                controls[field] = spin
                tab_controls.append(spin)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            card_layout.addLayout(grid)

            preset_combo = QtWidgets.QComboBox()
            preset_combo.setObjectName(f"axis_{key}_preset_combo")
            preset_combo.addItem("常用范围…", None)
            for preset_label, minimum, maximum, major in self.PRESETS[key]:
                preset_combo.addItem(preset_label, (minimum, maximum, major))
            preset_combo.currentIndexChanged.connect(
                lambda index, axis=key: self._preset_selected(axis, index)
            )
            controls["preset_combo"] = preset_combo
            card_layout.addWidget(preset_combo)
            card_layout.addStretch(1)

            tab_controls.append(auto)
            self.controls[key] = controls
            cards.addWidget(card, 1)
        root.addLayout(cards, 1)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("axisStatus")
        self.status_label.setProperty("valid", True)
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status_label)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setObjectName("dialogButtons")
        buttons.setProperty("uiRole", "dialogActions")
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.ok_button.setText("应用坐标")
        self.ok_button.setObjectName("primaryButton")
        self.ok_button.setProperty("uiRole", "primaryButton")
        self.ok_button.setDefault(True)
        cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for first, second in zip(tab_controls, tab_controls[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        for key, controls in self.controls.items():
            controls["min"].valueChanged.connect(lambda _value, axis=key: self._bounds_changed(axis))
            controls["max"].valueChanged.connect(lambda _value, axis=key: self._bounds_changed(axis))
            controls["span"].valueChanged.connect(lambda _value, axis=key: self._span_changed(axis))
            controls["major"].valueChanged.connect(lambda _value: self._validate())
            controls["auto"].toggled.connect(lambda _checked, axis=key: self._axis_mode_changed(axis))
            self._sync_axis_mode(key)
        self.auto_all_button.clicked.connect(self._set_all_auto)
        self.reset_button.clicked.connect(self._restore_defaults)
        self._validate()

    def _axis_mode_changed(self, axis: str) -> None:
        self._sync_axis_mode(axis)
        self._validate()

    def _sync_axis_mode(self, axis: str) -> None:
        controls = self.controls[axis]
        fixed = not controls["auto"].isChecked()
        for field in ("min", "max", "span"):
            controls[field].setEnabled(fixed)
        controls["preset_combo"].setEnabled(fixed)

    def _bounds_changed(self, axis: str) -> None:
        if self._updating:
            return
        controls = self.controls[axis]
        self._updating = True
        try:
            controls["span"].setValue(max(0.1, controls["max"].value() - controls["min"].value()))
        finally:
            self._updating = False
        self._validate()

    def _span_changed(self, axis: str) -> None:
        if self._updating:
            return
        controls = self.controls[axis]
        self._updating = True
        try:
            controls["max"].setValue(controls["min"].value() + controls["span"].value())
        finally:
            self._updating = False
        self._validate()

    def _apply_preset(self, axis: str, minimum: float, maximum: float, major: float) -> None:
        controls = self.controls[axis]
        self._updating = True
        try:
            controls["auto"].setChecked(False)
            controls["min"].setValue(minimum)
            controls["max"].setValue(maximum)
            controls["span"].setValue(maximum - minimum)
            controls["major"].setValue(major)
        finally:
            self._updating = False
        self._sync_axis_mode(axis)
        combo = controls["preset_combo"]
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self._validate()

    def _preset_selected(self, axis: str, index: int) -> None:
        combo = self.controls[axis]["preset_combo"]
        preset = combo.itemData(index)
        if preset is not None:
            self._apply_preset(axis, *preset)

    def _set_all_auto(self) -> None:
        for axis, controls in self.controls.items():
            controls["auto"].setChecked(True)
            self._sync_axis_mode(axis)
        self._validate()

    def _restore_defaults(self) -> None:
        for axis, defaults in self.DEFAULTS.items():
            self._apply_preset(axis, defaults["min"], defaults["max"], defaults["major"])

    def _validate(self) -> bool:
        errors = []
        summaries = []
        names = {"x": "时间", "left": "温度", "right": "RoR"}
        for axis, controls in self.controls.items():
            minimum = controls["min"].value()
            maximum = controls["max"].value()
            span = maximum - minimum
            major = controls["major"].value()
            if maximum <= minimum:
                errors.append(f"{names[axis]}轴最大值必须大于最小值")
            elif major <= 0:
                errors.append(f"{names[axis]}轴主刻度必须大于 0")
            elif not controls["auto"].isChecked() and major > span:
                errors.append(f"{names[axis]}轴固定范围的主刻度不能超过跨度")
            mode = "自动" if controls["auto"].isChecked() else "固定"
            summaries.append(f"{names[axis]} {minimum:g}–{maximum:g} · {major:g}/格 · {mode}")
        valid = not errors
        self.status_label.setProperty("valid", valid)
        self.status_label.setText("  |  ".join(summaries) if valid else "；".join(errors))
        self.ok_button.setEnabled(valid)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        return valid

    def accept(self) -> None:
        if self._validate():
            super().accept()

    def values(self) -> dict:
        result = {}
        for key, controls in self.controls.items():
            result[key] = {
                "min": controls["min"].value(),
                "max": controls["max"].value(),
                "span": controls["span"].value(),
                "major": controls["major"].value(),
                "auto": controls["auto"].isChecked(),
            }
        return result
