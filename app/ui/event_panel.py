from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


EVENT_BUTTONS = (
    ("CHARGE", "入豆"),
    ("TP", "转折"),
    ("YELLOW", "黄化"),
    ("FC_START", "一爆起"),
    ("FC_END", "一爆止"),
    ("SC_START", "二爆起"),
    ("SC_END", "二爆止"),
    ("DROP", "出锅"),
)

_LEGACY_EVENT_NAMES = {
    "CHARGE": "入豆",
    "TP": "回温点",
    "YELLOW": "转黄",
    "FC_START": "一爆开始",
    "FC_END": "一爆结束",
    "SC_START": "二爆开始",
    "SC_END": "二爆结束",
    "DROP": "排豆",
}


class _EventButton(QtWidgets.QPushButton):
    """QPushButton behavior with a two-level milestone label."""

    def __init__(self, main_text: str, event_code: str, parent=None) -> None:
        super().__init__(parent)
        self.main_text = main_text
        self.event_code = event_code
        self.elapsed_text = ""

    def set_presentation(self, main_text: str, event_code: str, elapsed_text: str = "") -> None:
        self.main_text = main_text
        self.event_code = event_code
        self.elapsed_text = elapsed_text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        compact = bool(self.property("compactMode"))
        return QtCore.QSize(max(hint.width(), 62), max(hint.height(), 34 if compact else 46))

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtWidgets.QStylePainter(self)
        option = QtWidgets.QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter.drawControl(QtWidgets.QStyle.ControlElement.CE_PushButton, option)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        state = str(self.property("eventState") or "pending")
        color = {
            "pending": QtGui.QColor("#8a94a6"),
            "recorded": QtGui.QColor("#f0821e"),
            "locked": QtGui.QColor("#e8544f" if self.event_code == "DROP" else "#f0821e"),
        }.get(state, QtGui.QColor("#8a94a6"))
        if not self.isEnabled() and state != "locked":
            color.setAlpha(105)

        compact = bool(self.property("compactMode"))
        content = self.rect().adjusted(4, 2, -4, -2)
        main_height = max(16, int(content.height() * (0.60 if compact else 0.62)))
        main_rect = QtCore.QRect(content.left(), content.top(), content.width(), main_height)
        code_rect = QtCore.QRect(
            content.left(),
            content.top() + main_height - 1,
            content.width(),
            max(10, content.height() - main_height + 1),
        )

        main_font = QtGui.QFont(self.font())
        main_font.setBold(True)
        main_font.setPointSizeF(max(8.5, self.font().pointSizeF() - (1.0 if compact else 0.2)))
        painter.setFont(main_font)
        painter.setPen(color)
        main = self.main_text if not self.elapsed_text else f"{self.main_text} {self.elapsed_text}"
        if compact:
            # One readable line in the compact deck; codes remain available
            # through the existing event model rather than a clipped second row.
            painter.drawText(content, QtCore.Qt.AlignmentFlag.AlignCenter, main)
            return
        painter.drawText(main_rect, QtCore.Qt.AlignmentFlag.AlignCenter, main)

        code_font = QtGui.QFont("Cascadia Code")
        code_font.setPointSizeF(7.0 if compact else 7.5)
        code_color = QtGui.QColor(color)
        code_color.setAlpha(175 if self.isEnabled() or state == "locked" else 85)
        painter.setFont(code_font)
        painter.setPen(code_color)
        painter.drawText(code_rect, QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop, self.event_code)


class EventPanel(QtWidgets.QWidget):
    """同一按钮完成里程碑的记录、撤销和重新记录。

    普通里程碑首次按下记录当前时间，再次按下撤销；DROP 记录后保持锁定。
    """

    event_requested = QtCore.Signal(str, float, str)
    event_cancelled = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("eventPanel")
        self.setProperty("uiRole", "eventMilestones")
        self.setAccessibleName("烘焙事件记录")
        self.setAccessibleDescription("八个事件按钮均为手动记录；普通事件可撤销重录，排豆记录后锁定")
        self.time_getter = lambda: 0.0
        self._names = dict(EVENT_BUTTONS)
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        self._recorded: set[str] = set()
        self._session_active = False
        self._ready_to_charge = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._layout = layout
        self._compact_mode = False
        self._button_columns = 4
        hint = QtWidgets.QLabel("待录：虚线 · 已录：琥珀实线 ● · DROP：红色锁定")
        hint.setObjectName("eventStateLegend")
        hint.setWordWrap(True)
        hint.setAccessibleName("事件状态图例")
        layout.addWidget(hint)
        self._hint = hint

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, (event_type, label) in enumerate(EVENT_BUTTONS):
            button = _EventButton(label, event_type)
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
            button.setObjectName("dropButton" if event_type == "DROP" else "eventButton")
            button.setProperty("uiRole", "eventMilestone")
            button.setCheckable(True)
            button.setMinimumHeight(46)
            button.setToolTip(
                "等待入豆时点击一次，正式计时从 00:00 开始；记录后锁定"
                if event_type == "CHARGE"
                else "仅记录：按下记录，再次按下撤销，之后可重新记录"
                if event_type != "DROP"
                else "仅记录：按下记录排豆事件，随后锁定且不可撤销"
            )
            button.toggled.connect(lambda checked, kind=event_type: self._toggle_event(kind, checked))
            grid.addWidget(button, index // 4, index % 4)
            self._buttons[event_type] = button
            self._set_button_presentation(event_type)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)
        self._grid = grid

        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("备注（绑定到下一次事件记录）")
        self.note_edit.setAccessibleName("下一次事件记录备注")
        layout.addWidget(self.note_edit)

        self.event_list = QtWidgets.QListWidget()
        self.event_list.setAccessibleName("本批次已记录事件列表")
        self.event_list.setMinimumHeight(6)
        self.event_list.setMaximumHeight(140)
        self.event_list.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        layout.addWidget(self.event_list, 1)

        self.set_session_active(False)

    def set_compact_mode(self, compact: bool) -> None:
        """Collapse secondary event history when the workbench is only 720P tall."""
        compact = bool(compact)
        if self._compact_mode == compact:
            return
        self._compact_mode = compact
        self.setProperty("compactMode", compact)
        self._hint.setVisible(not compact)
        self.event_list.setVisible(not compact)
        self._layout.setSpacing(4 if compact else 8)
        for button in self._buttons.values():
            button.setProperty("compactMode", compact)
            button.setMinimumHeight(34 if compact else 46)
            button.updateGeometry()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_button_columns(self, columns: int) -> None:
        """Keep the event strip horizontal on desktop and compact on narrower layouts."""
        columns = max(1, int(columns))
        if self._button_columns == columns:
            return
        self._button_columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, (event_type, _label) in enumerate(EVENT_BUTTONS):
            self._grid.addWidget(self._buttons[event_type], index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

    def set_current_time(self, getter) -> None:
        self.time_getter = getter

    def set_session_active(self, active: bool) -> None:
        self._session_active = bool(active)
        for event_type, button in self._buttons.items():
            is_recorded = event_type in self._recorded
            button.setEnabled(self._button_allowed(event_type, is_recorded))
            self._set_button_presentation(event_type)
        self.note_edit.setEnabled(self._session_active)

    def set_ready_to_charge(self, ready: bool) -> None:
        self._ready_to_charge = bool(ready)
        for event_type, button in self._buttons.items():
            button.setEnabled(self._button_allowed(event_type, event_type in self._recorded))
            self._set_button_presentation(event_type)

    def _button_allowed(self, event_type: str, recorded: bool) -> bool:
        if not self._session_active:
            return False
        if self._ready_to_charge:
            return event_type == "CHARGE" and not recorded
        if event_type == "CHARGE":
            return False
        return not (event_type == "DROP" and recorded)

    def set_events(self, events) -> None:
        self.event_list.clear()
        event_map = {
            str(item.get("event_type", "")).strip().upper(): item
            for item in events
        }
        self._recorded = set(event_map)
        for event_type, button in self._buttons.items():
            button.blockSignals(True)
            event = event_map.get(event_type)
            button.setChecked(event_type in self._recorded)
            button.setEnabled(self._button_allowed(event_type, event is not None))
            self._set_button_presentation(
                event_type,
                None if event is None else float(event.get("time_s", 0.0)),
            )
            button.blockSignals(False)
        for event in sorted(events, key=lambda item: float(item.get("time_s", 0))):
            self.add_event_row(event)
        self.note_edit.clear()

    def add_event_row(self, event) -> None:
        event_type = event["event_type"]
        name = self._names.get(event_type, event_type)
        text = f"{event['time_s']:.1f} 秒  |  {name}"
        if event.get("note"):
            text += f"  |  {event['note']}"
        self.event_list.addItem(text)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(round(float(seconds))))
        minutes = total // 60
        return f"{minutes:02d}:{total % 60:02d}"

    def _format_event_button_text(self, event_type: str, seconds: float | None = None) -> str:
        name = _LEGACY_EVENT_NAMES.get(event_type, self._names.get(event_type, event_type))
        if seconds is None or self._compact_mode:
            return name
        elapsed = self._format_elapsed(seconds)
        return f"{name} {elapsed}"

    def _set_button_presentation(self, event_type: str, seconds: float | None = None) -> None:
        button = self._buttons[event_type]
        recorded = event_type in self._recorded or button.isChecked()
        state = "locked" if event_type in ("CHARGE", "DROP") and recorded else "recorded" if recorded else "pending"
        if seconds is not None:
            button.setProperty("recordedSeconds", float(seconds))
        elif not recorded:
            button.setProperty("recordedSeconds", None)
        saved_seconds = button.property("recordedSeconds")
        shown_seconds = float(saved_seconds) if saved_seconds is not None and recorded else None
        button.setProperty("eventState", state)
        display_text = self._format_event_button_text(event_type, shown_seconds)
        button.setText(display_text)
        name = self._names.get(event_type, event_type)
        elapsed_text = self._format_elapsed(shown_seconds) if shown_seconds is not None else ""
        if isinstance(button, _EventButton):
            button.set_presentation(name, event_type, elapsed_text)
        if state == "locked":
            description = f"{name}已记录并锁定，不可再次操作"
        elif state == "recorded":
            description = f"{name}已记录；再次按下可撤销并重新记录"
        elif self._session_active:
            description = f"{name}待记录；按下只记录当前时间"
        else:
            description = f"{name}待记录；批次开始后可用"
        button.setAccessibleName(f"{event_type} {name}，{state}")
        button.setAccessibleDescription(description)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _toggle_event(self, event_type: str, checked: bool) -> None:
        if not self._session_active:
            return
        button = self._buttons[event_type]
        if event_type == "DROP":
            if not checked or event_type in self._recorded:
                button.blockSignals(True)
                button.setChecked(True)
                button.blockSignals(False)
                self._set_button_presentation(event_type)
                return
            event_time = float(self.time_getter())
            button.setProperty("recordedSeconds", event_time)
            self.event_requested.emit(event_type, event_time, self.note_edit.text().strip())
            self.note_edit.clear()
            self._recorded.add(event_type)
            button.blockSignals(True)
            button.setEnabled(False)
            button.blockSignals(False)
            self._set_button_presentation(event_type, event_time)
            return

        if checked:
            event_time = float(self.time_getter())
            button.setProperty("recordedSeconds", event_time)
            self._set_button_presentation(event_type, event_time)
            self.event_requested.emit(event_type, event_time, self.note_edit.text().strip())
            self.note_edit.clear()
            return

        self._recorded.discard(event_type)
        button.setProperty("recordedSeconds", None)
        self._set_button_presentation(event_type)
        self.event_cancelled.emit(event_type)
