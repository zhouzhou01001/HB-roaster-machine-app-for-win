from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class BatchInfoDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, values=None) -> None:
        super().__init__(parent)
        self.setObjectName("batchInfoDialog")
        self.setProperty("uiRole", "dialog")
        self.setWindowTitle("批次信息")
        self.values = values or {}
        self.resize(520, 440)
        self.setMinimumSize(440, 380)
        self.setSizeGripEnabled(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("批次信息")
        title.setObjectName("dialogTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("记录设备、操作者与烘焙重量信息")
        subtitle.setObjectName("subtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        badge = QtWidgets.QLabel("原：BatchInfoDialog")
        badge.setObjectName("originBadge")
        badge.setProperty("uiRole", "originBadge")
        header.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        card = QtWidgets.QGroupBox("批次档案")
        card.setObjectName("formCard")
        card.setProperty("uiRole", "formPanel")
        form = QtWidgets.QFormLayout(card)
        form.setContentsMargins(12, 16, 12, 12)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        card_badge = QtWidgets.QLabel("原：BatchInfoDialog")
        card_badge.setObjectName("panelBadge")
        card_badge.setProperty("uiRole", "panelBadge")
        form.addRow(card_badge)

        self.device_edit = QtWidgets.QLineEdit(self.values.get("device", ""))
        self.device_edit.setObjectName("deviceEdit")
        self.device_edit.setProperty("uiRole", "formField")
        self.operator_edit = QtWidgets.QLineEdit(self.values.get("operator", ""))
        self.operator_edit.setObjectName("operatorEdit")
        self.operator_edit.setProperty("uiRole", "formField")
        self.coffee_edit = QtWidgets.QLineEdit(self.values.get("coffee_name", ""))
        self.coffee_edit.setObjectName("coffeeEdit")
        self.coffee_edit.setProperty("uiRole", "formField")
        self.green_weight = QtWidgets.QDoubleSpinBox()
        self.green_weight.setObjectName("greenWeightSpin")
        self.green_weight.setProperty("uiRole", "numericField")
        self.green_weight.setRange(0.0, 10000.0)
        self.green_weight.setSuffix(" g")
        self.green_weight.setValue(float(self.values.get("green_weight", 0.0) or 0.0))
        self.roasted_weight = QtWidgets.QDoubleSpinBox()
        self.roasted_weight.setObjectName("roastedWeightSpin")
        self.roasted_weight.setProperty("uiRole", "numericField")
        self.roasted_weight.setRange(0.0, 10000.0)
        self.roasted_weight.setSuffix(" g")
        self.roasted_weight.setValue(float(self.values.get("roasted_weight", 0.0) or 0.0))

        rows = (("设备", self.device_edit), ("操作者", self.operator_edit), ("咖啡名称", self.coffee_edit), ("生豆重量", self.green_weight), ("熟豆重量", self.roasted_weight))
        for label_text, control in rows:
            label = QtWidgets.QLabel(label_text)
            label.setObjectName("fieldLabel")
            label.setProperty("uiRole", "fieldLabel")
            label.setBuddy(control)
            form.addRow(label, control)
        layout.addWidget(card)

        hint = QtWidgets.QLabel("重量字段以克为单位；这些信息仅用于当前批次档案与后续分析。")
        hint.setObjectName("hintPanel")
        hint.setProperty("uiRole", "hintPanel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.setObjectName("dialogButtons")
        btns.setProperty("uiRole", "dialogActions")
        ok_button = btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        cancel_button = btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        ok_button.setText("确定")
        ok_button.setObjectName("primaryButton")
        ok_button.setProperty("uiRole", "primaryButton")
        ok_button.setDefault(True)
        cancel_button.setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        tab_controls = [self.device_edit, self.operator_edit, self.coffee_edit, self.green_weight, self.roasted_weight]
        for first, second in zip(tab_controls, tab_controls[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)
        QtWidgets.QWidget.setTabOrder(self.roasted_weight, cancel_button)
        QtWidgets.QWidget.setTabOrder(cancel_button, ok_button)

    def payload(self):
        return {
            "device": self.device_edit.text(),
            "operator": self.operator_edit.text(),
            "coffee_name": self.coffee_edit.text(),
            "green_weight": self.green_weight.value(),
            "roasted_weight": self.roasted_weight.value(),
        }
