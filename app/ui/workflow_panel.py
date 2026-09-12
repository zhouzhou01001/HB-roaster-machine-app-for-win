"""Compact, embeddable workflow status panel for the main window right rail."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtWidgets


@dataclass(frozen=True)
class _WorkflowState:
    label: str
    description: str
    tone: str


_WORKFLOW_STATES: dict[str, _WorkflowState] = {
    "idle": _WorkflowState("待采集", "开始采集 → 自动开第 1 锅", "neutral"),
    "roasting": _WorkflowState("烘焙中", "事件记录 · DROP 后待结束", "active"),
    "await_end": _WorkflowState("待结束", "结束烘焙（先预览）→ 封存", "warning"),
    "between": _WorkflowState("锅次间隙", "开始烘焙 → 下一锅", "success"),
}


class WorkflowPanel(QtWidgets.QFrame):
    """Read-only rendering of the four-state roast workflow.

    The panel deliberately owns no workflow controller and performs no persistence.
    Call :meth:`set_state` when the application state changes.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workflowPanel")
        self.setProperty("uiRole", "panel")
        self.setAccessibleName("流程状态机")

        self._state = "idle"
        self._state_rows: dict[str, QtWidgets.QFrame] = {}
        self._height_compact = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        heading = QtWidgets.QVBoxLayout()
        heading.setSpacing(1)
        title = QtWidgets.QLabel("流程状态机")
        title.setObjectName("sectionTitle")
        title.setProperty("uiRole", "sectionTitle")
        self.subtitle = QtWidgets.QLabel("锅次切换可追溯")
        self.subtitle.setObjectName("workflowSubtitle")
        self.subtitle.setProperty("uiRole", "secondaryText")
        self.subtitle.setMinimumWidth(0)
        self.subtitle.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        heading.addWidget(title)
        heading.addWidget(self.subtitle)
        header.addLayout(heading, 1)

        self.status_badge = QtWidgets.QLabel()
        self.status_badge.setObjectName("workflowStatusBadge")
        self.status_badge.setProperty("uiRole", "statusBadge")
        self.status_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.status_badge.setMinimumWidth(68)
        header.addWidget(self.status_badge, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        counters = QtWidgets.QHBoxLayout()
        counters.setSpacing(4)
        current_card, self.current_charge_label = self._counter_card("当前锅次")
        total_card, self.total_charge_label = self._counter_card("累计锅次")
        counters.addWidget(current_card, 1)
        counters.addWidget(total_card, 1)
        root.addLayout(counters)
        self._counter_cards = (current_card, total_card)

        state_list = QtWidgets.QVBoxLayout()
        state_list.setSpacing(2)
        self._description_labels: list[QtWidgets.QLabel] = []
        for code, definition in _WORKFLOW_STATES.items():
            row = QtWidgets.QFrame()
            row.setObjectName("workflowStateRow")
            row.setProperty("uiRole", "workflowStateRow")
            row.setProperty("workflowState", code)
            row.setProperty("active", False)
            row.setAccessibleName(f"{definition.label}：{definition.description}")

            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(6, 3, 6, 3)
            row_layout.setSpacing(5)
            marker = QtWidgets.QLabel("●")
            marker.setObjectName("workflowStateMarker")
            marker.setProperty("uiRole", "stateMarker")
            marker.setFixedWidth(10)
            name = QtWidgets.QLabel(definition.label)
            name.setObjectName("workflowStateName")
            name.setProperty("uiRole", "fieldLabel")
            name.setMinimumWidth(56)
            description = QtWidgets.QLabel(definition.description)
            description.setObjectName("workflowStateDescription")
            description.setProperty("uiRole", "secondaryText")
            description.setWordWrap(False)
            description.setMinimumWidth(0)
            description.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
            row_layout.addWidget(marker)
            row_layout.addWidget(name)
            row_layout.addWidget(description, 1)
            state_list.addWidget(row)
            self._state_rows[code] = row
            self._description_labels.append(description)
        root.addLayout(state_list)

        self.set_state("idle", total_charges=0, current_charge=None)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_state(event.size().width() < 260)

    def set_compact_mode(self, compact: bool) -> None:
        """Keep the current workflow state visible in short workbench layouts."""

        self._height_compact = bool(compact)
        self._apply_responsive_state(self.width() < 260)

    def _apply_responsive_state(self, width_compact: bool) -> None:
        compact = bool(width_compact or self._height_compact)
        self.setProperty("compactMode", compact)
        self.subtitle.setVisible(not compact)
        for description in self._description_labels:
            description.setVisible(not compact)
        for card in self._counter_cards:
            card.setVisible(not self._height_compact)
        for row in self._state_rows.values():
            row.setVisible(not self._height_compact)
        margins = 5 if self._height_compact else 8
        self.layout().setContentsMargins(margins, margins, margins, margins)
        self.layout().setSpacing(2 if self._height_compact else 6)
        self.updateGeometry()

    @property
    def current_state(self) -> str:
        """Return the currently rendered workflow state code."""

        return self._state

    @property
    def state_rows(self) -> dict[str, QtWidgets.QFrame]:
        """Return a shallow copy of state-row widgets for inspection or styling."""

        return dict(self._state_rows)

    def set_state(
        self,
        state: str,
        *,
        total_charges: int = 0,
        current_charge: int | None = None,
    ) -> None:
        """Render a workflow snapshot without changing application state elsewhere."""

        if state not in _WORKFLOW_STATES:
            allowed = ", ".join(_WORKFLOW_STATES)
            raise ValueError(f"unknown workflow state {state!r}; expected one of: {allowed}")
        if total_charges < 0:
            raise ValueError("total_charges must be non-negative")
        if current_charge is not None and current_charge < 1:
            raise ValueError("current_charge must be positive when provided")

        self._state = state
        definition = _WORKFLOW_STATES[state]
        self.setProperty("workflowState", state)
        self.status_badge.setText(f"● {definition.label}")
        self.status_badge.setAccessibleName(f"当前状态：{definition.label}")
        self.status_badge.setProperty("workflowState", state)
        self.status_badge.setProperty("statusTone", definition.tone)
        self.current_charge_label.setText("—" if current_charge is None else f"第 {current_charge} 锅")
        self.total_charge_label.setText(f"{total_charges} 锅")

        for code, row in self._state_rows.items():
            is_active = code == state
            row.setProperty("active", is_active)
            row.setProperty("statusTone", definition.tone if is_active else "muted")
            self._refresh_style(row)
        self._refresh_style(self.status_badge)
        self._refresh_style(self)

    @staticmethod
    def _counter_card(label_text: str) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
        card = QtWidgets.QFrame()
        card.setObjectName("summaryCard")
        card.setProperty("uiRole", "summaryCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)
        label = QtWidgets.QLabel(label_text)
        label.setObjectName("summaryLabel")
        label.setProperty("uiRole", "fieldLabel")
        value = QtWidgets.QLabel("—")
        value.setObjectName("summaryValue")
        value.setProperty("uiRole", "numericSummary")
        value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        layout.addWidget(value)
        return card, value

    @staticmethod
    def _refresh_style(widget: QtWidgets.QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
