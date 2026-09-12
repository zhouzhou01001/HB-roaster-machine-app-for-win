from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class ActionPanel(QtWidgets.QWidget):
    action_added = QtCore.Signal(float, float, float, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("actionPanel")
        self.setProperty("uiRole", "manualRecordControls")
        self.setAccessibleName("机台手动操作记录")
        self.setAccessibleDescription("所有调节只写入操作记录，不向烘焙设备发送控制指令")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._layout = layout
        self._compact_mode = False
        self.setStyleSheet("""
            QFrame#actionControlCard {
                background: #242932;
                border: 1px solid #38404c;
                border-radius: 6px;
            }
            QLabel#actionControlLabel { color: #e7eaf0; font-weight: 600; }
            QLabel#actionControlMeta, QLabel#actionScale { color: #7f8998; font-size: 9px; }
            QDoubleSpinBox#actionReadout {
                color: #f2f4f7;
                background: #171b21;
                border: 1px solid #3b4451;
                border-radius: 4px;
                padding: 2px 4px;
                font-family: "Cascadia Code", Consolas, monospace;
                font-weight: 700;
            }
            QDoubleSpinBox#actionReadout::up-button, QDoubleSpinBox#actionReadout::down-button {
                width: 0px; border: 0;
            }
            QToolButton#actionStepButton {
                min-width: 18px; max-width: 18px;
                min-height: 18px; max-height: 18px;
                padding: 0; margin: 0;
                color: #d9dde4;
                background: #303743;
                border: 1px solid #4a5361;
                border-radius: 3px;
                font-weight: 700;
            }
            QToolButton#actionStepButton:hover { border-color: #f0821e; color: #f0821e; }
            QSlider::groove:horizontal { height: 6px; background: #3c4450; border-radius: 3px; }
            QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; }
            QSlider#gasSlider::sub-page:horizontal, QSlider#gasSlider::handle:horizontal { background: #f5d442; }
            QSlider#damperSlider::sub-page:horizontal, QSlider#damperSlider::handle:horizontal { background: #35c98e; }
            QSlider#rpmSlider::sub-page:horizontal, QSlider#rpmSlider::handle:horizontal { background: #ec6ba9; }
            QLabel#recordOnlyNotice { color: #9ca4b0; }
        """)

        record_notice = QtWidgets.QLabel("仅记录 · 不向设备发送控制指令")
        record_notice.setObjectName("recordOnlyNotice")
        record_notice.setWordWrap(True)
        record_notice.setAccessibleName("仅记录提示")
        record_notice.setAccessibleDescription("软件只记录人工机台操作")
        layout.addWidget(record_notice)
        self._record_notice = record_notice
        self._control_cards: list[QtWidgets.QFrame] = []

        self.gas_spin = QtWidgets.QDoubleSpinBox()
        self.gas_spin.setRange(0.0, 50.0)
        self.gas_spin.setDecimals(1)
        self.gas_spin.setSingleStep(0.5)
        self.gas_spin.setSuffix(" mbar")
        self.gas_spin.setAccessibleName("燃气压力记录值，单位 mbar")
        self.gas_spin.setToolTip("记录燃气压力：0–50 mbar，步进 0.5；不发送控制指令")
        self.gas_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gas_slider.setRange(0, 100)
        self.gas_slider.setSingleStep(1)
        self.gas_slider.setPageStep(10)
        self.gas_slider.setObjectName("gasSlider")
        self.gas_slider.setAccessibleName("燃气压力记录滑块")
        self.gas_slider.setAccessibleDescription("范围 0 到 50 mbar，每步 0.5 mbar，仅记录")
        gas_card = self._control_card(
            "燃气压力",
            "mbar · 0–50 / 步进 0.5",
            self.gas_spin,
            self.gas_slider,
            "0                 25                 50",
            "gas",
        )
        self._control_cards.append(gas_card)
        layout.addWidget(gas_card)

        self.damper_spin = QtWidgets.QDoubleSpinBox()
        self.damper_spin.setRange(0.0, 10.0)
        self.damper_spin.setDecimals(0)
        self.damper_spin.setSingleStep(1.0)
        self.damper_spin.setPrefix("第 ")
        self.damper_spin.setSuffix(" 档")
        self.damper_spin.setAccessibleName("风门记录档位")
        self.damper_spin.setToolTip("独立记录风门第 1–10 档；不发送控制指令")
        self.damper_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.damper_slider.setRange(0, 10)
        self.damper_slider.setSingleStep(1)
        self.damper_slider.setPageStep(1)
        self.damper_slider.setObjectName("damperSlider")
        self.damper_slider.setAccessibleName("风门独立档位记录滑块")
        self.damper_slider.setAccessibleDescription("第 1 到第 10 档，仅记录")
        damper_card = self._control_card(
            "风门开度",
            "独立 · 0–10 档",
            self.damper_spin,
            self.damper_slider,
            "0  1  2  3  4  5  6  7  8  9  10",
            "damper",
        )
        self._control_cards.append(damper_card)
        layout.addWidget(damper_card)

        self.rpm_spin = QtWidgets.QDoubleSpinBox()
        self.rpm_spin.setRange(0.0, 10.0)
        self.rpm_spin.setDecimals(0)
        self.rpm_spin.setSingleStep(1.0)
        self.rpm_spin.setPrefix("第 ")
        self.rpm_spin.setSuffix(" 档")
        self.rpm_spin.setAccessibleName("滚筒转速记录档位")
        self.rpm_spin.setToolTip("独立记录滚筒转速第 1–10 档；不发送控制指令")
        self.rpm_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.rpm_slider.setRange(0, 10)
        self.rpm_slider.setSingleStep(1)
        self.rpm_slider.setPageStep(1)
        self.rpm_slider.setObjectName("rpmSlider")
        self.rpm_slider.setAccessibleName("滚筒转速独立档位记录滑块")
        self.rpm_slider.setAccessibleDescription("第 1 到第 10 档，仅记录，与风门互不联动")
        rpm_card = self._control_card(
            "滚筒转速",
            "独立 · 0–10 档",
            self.rpm_spin,
            self.rpm_slider,
            "0  1  2  3  4  5  6  7  8  9  10",
            "rpm",
        )
        self._control_cards.append(rpm_card)
        layout.addWidget(rpm_card)

        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setMinimumWidth(60)
        self.note_edit.setPlaceholderText("本次操作备注（可选）")
        self.note_edit.setAccessibleName("本次人工操作备注")
        layout.addWidget(self.note_edit)

        self.add_btn = QtWidgets.QPushButton("记录操作")
        self.add_btn.setText("仅记录本次操作")
        self.add_btn.setObjectName("recordActionButton")
        self.add_btn.setAccessibleName("记录本次人工操作")
        self.add_btn.setAccessibleDescription("保存燃气、风门和转速记录，不控制设备")
        self.add_btn.setToolTip("写入操作记录，不向设备发送任何指令")
        layout.addWidget(self.add_btn)

        self.setMinimumWidth(0)
        self.setMinimumHeight(150)

        self.gas_slider.valueChanged.connect(lambda value: self.gas_spin.setValue(value / 2.0))
        self.gas_spin.valueChanged.connect(lambda value: self.gas_slider.setValue(int(round(value * 2.0))))
        self.damper_slider.valueChanged.connect(self.damper_spin.setValue)
        self.damper_spin.valueChanged.connect(lambda value: self.damper_slider.setValue(int(round(value))))
        self.rpm_slider.valueChanged.connect(self.rpm_spin.setValue)
        self.rpm_spin.valueChanged.connect(lambda value: self.rpm_slider.setValue(int(round(value))))

        self.time_getter = lambda: 0.0
        self.add_btn.clicked.connect(self._emit)

    def set_compact_mode(self, compact: bool) -> None:
        """Keep the narrow 720P workbench fully operable without vertical clipping."""
        compact = bool(compact)
        if self._compact_mode == compact:
            return
        self._compact_mode = compact
        self.setProperty("compactMode", compact)
        self._record_notice.setVisible(not compact)
        self.note_edit.setVisible(not compact)
        self._layout.setSpacing(4 if compact else 8)
        margin = 4 if compact else 8
        for card in self._control_cards:
            card.layout().setContentsMargins(margin, 2 if compact else margin, margin, 2 if compact else margin)
            card.layout().setSpacing(0 if compact else 4)
            card._meta_label.setVisible(not compact)
            card._scale.setVisible(not compact)
            card._heading_layout.removeWidget(card._slider)
            card.layout().removeWidget(card._slider)
            card.layout().insertWidget(2, card._slider)
            card._slider.setVisible(True)
            card._slider.setMinimumWidth(180 if compact else 220)
            card._slider.setMinimumHeight(20 if compact else 22)
            card._readout.setFixedWidth(74 if compact else 82)
        self.add_btn.setText("记录操作" if compact else "仅记录本次操作")
        self.setMinimumHeight(0 if compact else 150)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    @staticmethod
    def _control_card(
        title: str,
        meta: str,
        readout: QtWidgets.QDoubleSpinBox,
        slider: QtWidgets.QSlider,
        scale_text: str,
        tone: str,
    ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("actionControlCard")
        card.setProperty("uiRole", "manualRecordControl")
        card.setProperty("controlTone", tone)
        card.setAccessibleName(title)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(4)

        heading = QtWidgets.QHBoxLayout()
        heading.setSpacing(4)
        label = QtWidgets.QLabel(title)
        label.setObjectName("actionControlLabel")
        label.setProperty("uiRole", "fieldLabel")
        meta_label = QtWidgets.QLabel(meta)
        meta_label.setObjectName("actionControlMeta")
        meta_label.setProperty("uiRole", "secondaryText")
        heading.addWidget(label)
        heading.addStretch(1)
        readout.setObjectName("actionReadout")
        readout.setProperty("uiRole", "numericField")
        readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        readout.setFixedWidth(82)
        decrement = QtWidgets.QToolButton()
        decrement.setObjectName("actionStepButton")
        decrement.setProperty("uiRole", "stepButton")
        decrement.setText("−")
        decrement.setFixedSize(18, 18)
        decrement.setAutoRepeat(True)
        decrement.setAccessibleName(f"减少{title}")
        increment = QtWidgets.QToolButton()
        increment.setObjectName("actionStepButton")
        increment.setProperty("uiRole", "stepButton")
        increment.setText("+")
        increment.setFixedSize(18, 18)
        increment.setAutoRepeat(True)
        increment.setAccessibleName(f"增加{title}")
        decrement.clicked.connect(readout.stepDown)
        increment.clicked.connect(readout.stepUp)
        heading.addWidget(readout)
        heading.addWidget(decrement)
        heading.addWidget(increment)
        card_layout.addLayout(heading)

        card_layout.addWidget(meta_label)
        slider.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        slider.setMinimumWidth(220)
        slider.setMinimumHeight(22)
        card_layout.addWidget(slider)

        scale = QtWidgets.QLabel(scale_text)
        scale.setObjectName("actionScale")
        scale.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        scale.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        card_layout.addWidget(scale)
        card._meta_label = meta_label
        card._heading_layout = heading
        card._slider = slider
        card._scale = scale
        card._readout = readout
        card._decrement_button = decrement
        card._increment_button = increment
        return card

    def set_current_time(self, getter):
        self.time_getter = getter

    def _emit(self) -> None:
        self.action_added.emit(
            self.gas_spin.value() / 10.0,
            self.damper_spin.value(),
            self.rpm_spin.value(),
            self.note_edit.text().strip(),
        )
        self.note_edit.clear()
