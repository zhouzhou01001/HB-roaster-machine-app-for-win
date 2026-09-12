"""Read-only archive summary panel for the main window right rail."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6 import QtCore, QtWidgets


_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("identity", "最近锅次 / 批次"),
    ("bean", "豆种"),
    ("weight", "投豆量"),
    ("duration", "烘焙时长"),
    ("drop", "DROP"),
    ("fc_start", "FC_START"),
    ("gas_actions", "燃气操作"),
    ("damper_actions", "风门操作"),
    ("rpm_actions", "转速操作"),
)


class ArchiveSummaryPanel(QtWidgets.QFrame):
    """Render the latest archived roast from caller-supplied in-memory data.

    ``set_summary`` replaces the snapshot. ``update`` merges a partial snapshot;
    its zero-argument and native Qt overload forms still delegate to QWidget so
    normal repaint behavior remains available.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("archiveSummaryPanel")
        self.setProperty("uiRole", "panelSecondary")
        self.setAccessibleName("最近封存锅次摘要")
        self._summary: dict[str, Any] = {}
        self._height_compact = False
        self.value_labels: dict[str, QtWidgets.QLabel] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        heading = QtWidgets.QVBoxLayout()
        heading.setSpacing(1)
        title = QtWidgets.QLabel("锅次档案")
        title.setObjectName("sectionTitle")
        title.setProperty("uiRole", "sectionTitle")
        self.subtitle = QtWidgets.QLabel("最近封存 · 只读摘要")
        self.subtitle.setObjectName("archiveSummarySubtitle")
        self.subtitle.setProperty("uiRole", "secondaryText")
        heading.addWidget(title)
        heading.addWidget(self.subtitle)
        header.addLayout(heading, 1)

        self.saved_badge = QtWidgets.QLabel("只读")
        self.saved_badge.setObjectName("archiveStatusBadge")
        self.saved_badge.setProperty("uiRole", "statusBadge")
        self.saved_badge.setProperty("statusTone", "neutral")
        self.saved_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.saved_badge.setMinimumWidth(54)
        header.addWidget(self.saved_badge, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.empty_label = QtWidgets.QLabel("暂无已封存锅次")
        self.empty_label.setObjectName("archiveEmptyState")
        self.empty_label.setProperty("uiRole", "emptyState")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(36)
        root.addWidget(self.empty_label)

        self.summary_host = QtWidgets.QWidget()
        self.summary_host.setObjectName("archiveSummaryGrid")
        self.summary_host.setProperty("uiRole", "summaryGrid")
        grid = QtWidgets.QGridLayout(self.summary_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        for index, (key, label_text) in enumerate(_FIELD_LABELS):
            card = QtWidgets.QFrame()
            card.setObjectName("summaryCard")
            card.setProperty("uiRole", "summaryCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(6, 4, 6, 4)
            card_layout.setSpacing(0)
            label = QtWidgets.QLabel(label_text)
            label.setObjectName("summaryLabel")
            label.setProperty("uiRole", "fieldLabel")
            value = QtWidgets.QLabel("—")
            value.setObjectName(f"archive_{key}")
            value.setProperty("uiRole", "numericSummary")
            value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            value.setWordWrap(False)
            card_layout.addWidget(label)
            card_layout.addWidget(value)
            grid.addWidget(card, index // 2, index % 2)
            self.value_labels[key] = value
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.summary_host.hide()
        root.addWidget(self.summary_host)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        compact = event.size().width() < 260 or self._height_compact
        self.setProperty("compactMode", compact)
        self.subtitle.setVisible(not compact)

    def set_compact_mode(self, compact: bool) -> None:
        """Reduce the rail card to its saved-state header on short displays."""

        self._height_compact = bool(compact)
        self.setProperty("compactMode", self._height_compact or self.width() < 260)
        self.subtitle.setVisible(not self.property("compactMode"))
        self.empty_label.setVisible(not self._height_compact and not bool(self._summary))
        self.summary_host.setVisible(not self._height_compact and bool(self._summary))
        margins = 5 if self._height_compact else 8
        self.layout().setContentsMargins(margins, margins, margins, margins)
        self.layout().setSpacing(2 if self._height_compact else 6)
        self.updateGeometry()

    def set_summary(
        self,
        summary: Mapping[str, Any] | None = None,
        /,
        **values: Any,
    ) -> None:
        """Replace the displayed snapshot using caller-owned data only."""

        snapshot = dict(summary or {})
        snapshot.update(values)
        self._summary = snapshot
        self._render_summary()

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        """Merge archive fields, or delegate native QWidget repaint overloads.

        ``panel.update({"duration_s": 120})`` and
        ``panel.update(duration_s=120)`` update the read-only snapshot. Calling
        ``panel.update()`` (or passing a QRect/QRegion) keeps Qt's repaint API.
        """

        is_mapping_update = (
            (not args and bool(kwargs))
            or (len(args) == 1 and isinstance(args[0], Mapping))
        )
        if is_mapping_update:
            changes = dict(args[0]) if args else {}
            changes.update(kwargs)
            merged = dict(self._summary)
            merged.update(changes)
            self.set_summary(merged)
            return
        super().update(*args, **kwargs)

    def summary(self) -> dict[str, Any]:
        """Return a defensive copy of the raw snapshot currently rendered."""

        return dict(self._summary)

    def _render_summary(self) -> None:
        has_summary = bool(self._summary)
        self.empty_label.setVisible(not self._height_compact and not has_summary)
        self.summary_host.setVisible(not self._height_compact and has_summary)
        self.saved_badge.setText("已保存" if has_summary else "只读")
        self.saved_badge.setProperty("statusTone", "success" if has_summary else "neutral")

        rendered = {
            "identity": self._format_identity(),
            "bean": self._format_bean(),
            "weight": self._format_weight(self._first("charge_weight_g", "weight_g", "weight")),
            "duration": self._format_time(self._first("duration_s", "duration")),
            "drop": self._format_drop(),
            "fc_start": self._format_time(self._first("fc_start_s", "fc_start", "FC_START")),
            "gas_actions": self._format_count(self._first("gas_action_count", "gas_actions")),
            "damper_actions": self._format_count(self._first("damper_action_count", "damper_actions")),
            "rpm_actions": self._format_count(self._first("rpm_action_count", "rpm_actions")),
        }
        for key, value in rendered.items():
            self.value_labels[key].setText(value)
        self._refresh_style(self.saved_badge)

    def _format_identity(self) -> str:
        charge = self._first("charge_number", "charge_no", "roast_number")
        batch = self._first("batch_number", "batch_no", "batch")
        parts: list[str] = []
        if charge not in (None, ""):
            parts.append(f"#{charge}")
        if batch not in (None, ""):
            if isinstance(batch, int):
                parts.append(f"Batch #{batch:04d}")
            else:
                text = str(batch).strip()
                parts.append(text if text.lower().startswith("batch") else f"Batch #{text}")
        return " · ".join(parts) or "—"

    def _format_bean(self) -> str:
        bean = self._first("bean", "coffee_name", "bean_name", "coffee")
        origin = self._first("bean_origin", "origin")
        parts = [str(value).strip() for value in (bean, origin) if value not in (None, "")]
        return " · ".join(parts) or "—"

    def _format_drop(self) -> str:
        time_text = self._format_time(self._first("drop_time_s", "drop_time"))
        temperature = self._first("drop_bt", "drop_temperature", "drop_temp_c")
        temperature_text = self._format_temperature(temperature)
        values = [value for value in (time_text, temperature_text) if value != "—"]
        return " · ".join(values) or "—"

    def _first(self, *keys: str) -> Any:
        for key in keys:
            if key in self._summary and self._summary[key] is not None:
                return self._summary[key]
        return None

    @staticmethod
    def _format_weight(value: Any) -> str:
        if value in (None, ""):
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:g} g"
        return str(value)

    @staticmethod
    def _format_time(value: Any) -> str:
        if value in (None, ""):
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = max(0, int(round(value)))
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        return str(value)

    @staticmethod
    def _format_temperature(value: Any) -> str:
        if value in (None, ""):
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:.1f} °C"
        return str(value)

    @staticmethod
    def _format_count(value: Any) -> str:
        if value in (None, ""):
            return "—"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{int(value)} 次"
        return str(value)

    @staticmethod
    def _refresh_style(widget: QtWidgets.QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        QtWidgets.QWidget.update(widget)
