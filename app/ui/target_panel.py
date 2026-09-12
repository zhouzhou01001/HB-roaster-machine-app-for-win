from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class TargetPanel(QtWidgets.QWidget):
    targets_changed = QtCore.Signal(float, float)

    def __init__(self, yellow_c: float = 150.0, fc_c: float = 195.0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("targetPanel")
        self.setAccessibleName("里程碑 ETA 预测面板")
        self.setAccessibleDescription("设置转黄与一爆目标温度，并显示统一格式的预计到达时间")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        title = QtWidgets.QLabel("里程碑 ETA 预测")
        title.setObjectName("targetPanelTitle")
        badge = QtWidgets.QLabel("原：TargetPanel")
        badge.setObjectName("moduleBadge")
        badge.setAccessibleName("原模块 TargetPanel")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(badge)
        layout.addLayout(header)

        divider = QtWidgets.QFrame()
        divider.setObjectName("targetDivider")
        divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        layout.addWidget(divider)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.yellow_spin = QtWidgets.QDoubleSpinBox()
        self.yellow_spin.setRange(80.0, 230.0)
        self.yellow_spin.setDecimals(1)
        self.yellow_spin.setSingleStep(0.5)
        self.yellow_spin.setSuffix(" °C")
        self.yellow_spin.setValue(yellow_c)
        self.yellow_spin.setObjectName("targetReadout")
        self.yellow_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.yellow_spin.setAccessibleName("目标转黄温度")
        self.fc_spin = QtWidgets.QDoubleSpinBox()
        self.fc_spin.setRange(100.0, 250.0)
        self.fc_spin.setDecimals(1)
        self.fc_spin.setSingleStep(0.5)
        self.fc_spin.setSuffix(" °C")
        self.fc_spin.setValue(fc_c)
        self.fc_spin.setObjectName("targetReadout")
        self.fc_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.fc_spin.setAccessibleName("目标一爆开始温度")
        form.addRow("目标转黄", self.yellow_spin)
        form.addRow("目标一爆", self.fc_spin)
        layout.addLayout(form)
        self.yellow_eta = QtWidgets.QLabel("预计转黄：等待数据")
        self.fc_eta = QtWidgets.QLabel("预计一爆：等待数据")
        for label, accessible_name in (
            (self.yellow_eta, "预计转黄时间"),
            (self.fc_eta, "预计一爆开始时间"),
        ):
            label.setObjectName("forecastLabel")
            label.setAccessibleName(accessible_name)
            label.setAccessibleDescription("等待足够的烘焙数据进行预测")
            layout.addWidget(self._forecast_card(label))
        self.yellow_spin.valueChanged.connect(self._emit_targets)
        self.fc_spin.valueChanged.connect(self._emit_targets)

    @staticmethod
    def _forecast_card(label: QtWidgets.QLabel) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("forecastCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.addWidget(label)
        return card

    def values(self) -> tuple[float, float]:
        return self.yellow_spin.value(), self.fc_spin.value()

    def set_forecast(self, yellow_eta: float | None, fc_eta: float | None) -> None:
        self.yellow_eta.setText(self._format_eta("预计转黄", yellow_eta))
        self.fc_eta.setText(self._format_eta("预计一爆", fc_eta))
        self.yellow_eta.setAccessibleDescription(
            "无法预测" if yellow_eta is None else f"约 {int(yellow_eta // 60)} 分 {int(yellow_eta % 60)} 秒"
        )
        self.fc_eta.setAccessibleDescription(
            "无法预测" if fc_eta is None else f"约 {int(fc_eta // 60)} 分 {int(fc_eta % 60)} 秒"
        )

    def _emit_targets(self) -> None:
        yellow, fc = self.values()
        if fc <= yellow:
            self.fc_spin.blockSignals(True)
            self.fc_spin.setValue(yellow + 1.0)
            self.fc_spin.blockSignals(False)
            yellow, fc = self.values()
        self.targets_changed.emit(yellow, fc)

    @staticmethod
    def _format_eta(title: str, seconds: float | None) -> str:
        if seconds is None:
            return f"{title}：无法预测"
        return f"{title}：约 {int(seconds // 60):02d}:{int(seconds % 60):02d}"
