from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from PySide6 import QtCore, QtGui, QtWidgets


TAB_NAMES = ("结束烘焙", "导出曲线", "保存批次", "回放", "锅次对比", "配置变更")
CSV_HEADERS = ("time_s", "ET_C", "BT_C", "IT_C", "XT_C", "GAS_mbar", "DAM", "SPD")


def _text(value: Any, fallback: str = "--") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _section(snapshot: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


class _SparklineWidget(QtWidgets.QWidget):
    """Small read-only curve preview drawn exclusively from snapshot values."""

    def __init__(self, series: Iterable[Any] = (), parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("previewSparkline")
        self.setProperty("uiRole", "chartPreview")
        self.setAccessibleName("只读曲线缩略图")
        self.setMinimumHeight(110)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self._series = self._normalize_series(series)

    @staticmethod
    def _normalize_series(series: Iterable[Any]) -> list[list[QtCore.QPointF]]:
        rows = list(series or [])
        if rows and isinstance(rows[0], Mapping) and "series" in rows[0]:
            groups = [item.get("series", []) for item in rows]
        elif rows and isinstance(rows[0], (list, tuple)) and rows[0] and isinstance(rows[0][0], (list, tuple, Mapping)):
            groups = rows
        else:
            groups = [rows]
        normalized: list[list[QtCore.QPointF]] = []
        for group in groups:
            points: list[QtCore.QPointF] = []
            for index, item in enumerate(group or []):
                if isinstance(item, Mapping):
                    x_value = item.get("time_s", item.get("x", index))
                    y_value = item.get("bt", item.get("value", item.get("y")))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    x_value, y_value = item[0], item[1]
                else:
                    x_value, y_value = index, item
                try:
                    points.append(QtCore.QPointF(float(x_value), float(y_value)))
                except (TypeError, ValueError):
                    continue
            if points:
                normalized.append(points)
        return normalized

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        frame = self.rect().adjusted(8, 8, -8, -8)
        painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Mid), 1))
        painter.drawRect(frame)
        if not self._series:
            painter.setPen(palette.color(QtGui.QPalette.ColorRole.PlaceholderText))
            painter.drawText(frame, QtCore.Qt.AlignmentFlag.AlignCenter, "暂无曲线数据")
            return
        all_points = [point for series in self._series for point in series]
        min_x = min(point.x() for point in all_points)
        max_x = max(point.x() for point in all_points)
        min_y = min(point.y() for point in all_points)
        max_y = max(point.y() for point in all_points)
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        colors = (
            palette.color(QtGui.QPalette.ColorRole.Highlight),
            palette.color(QtGui.QPalette.ColorRole.PlaceholderText),
        )
        for index, series in enumerate(self._series):
            path = QtGui.QPainterPath()
            for point_index, point in enumerate(series):
                x = frame.left() + ((point.x() - min_x) / span_x) * frame.width()
                y = frame.bottom() - ((point.y() - min_y) / span_y) * frame.height()
                if point_index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            pen = QtGui.QPen(colors[index % len(colors)], 2)
            if index:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)


class _TimelineWidget(QtWidgets.QWidget):
    def __init__(self, events: Iterable[Mapping[str, Any]], duration_s: float, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("previewTimeline")
        self.setProperty("uiRole", "timelinePreview")
        self.setAccessibleName("只读回放时间轴")
        self.setFixedHeight(30)
        self._events = list(events or [])
        self._duration_s = max(1.0, float(duration_s or 720.0))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        palette = self.palette()
        line_rect = self.rect().adjusted(8, 13, -8, -12)
        painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Mid), 4, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
        painter.drawLine(line_rect.left(), line_rect.center().y(), line_rect.right(), line_rect.center().y())
        painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Highlight), 2))
        for item in self._events:
            try:
                ratio = min(1.0, max(0.0, float(item.get("time_s", 0.0)) / self._duration_s))
            except (TypeError, ValueError):
                continue
            x = line_rect.left() + int(line_rect.width() * ratio)
            painter.drawLine(x, 5, x, self.height() - 5)


class PreviewCenterDialog(QtWidgets.QDialog):
    """Render a prebuilt, read-only snapshot and emit confirmation intent only."""

    confirm_requested = QtCore.Signal(dict)

    def __init__(self, snapshot: dict, parent=None) -> None:
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot 必须是预先构建的 dict")
        super().__init__(parent)
        self._snapshot = deepcopy(snapshot)
        mode = str(self._snapshot.get("mode", "preview")).strip().lower()
        self._confirmation_mode = mode in {"confirm", "pending", "pending_confirmation", "待确认"}
        confirmation_tab = self._snapshot.get(
            "confirmation_tab",
            self._snapshot.get("confirm_tab", TAB_NAMES[0]),
        )
        if isinstance(confirmation_tab, str) and confirmation_tab in TAB_NAMES:
            confirmation_tab = TAB_NAMES.index(confirmation_tab)
        try:
            self._confirmation_tab_index = max(0, min(len(TAB_NAMES) - 1, int(confirmation_tab)))
        except (TypeError, ValueError):
            self._confirmation_tab_index = 0
        self._confirmation_tab_name = TAB_NAMES[self._confirmation_tab_index]

        self.setObjectName("previewCenterDialog")
        self.setProperty("uiRole", "dialog")
        self.setProperty("previewMode", "confirm" if self._confirmation_mode else "preview")
        self.setWindowTitle("统一只读预览")
        self.setModal(True)
        self.resize(900, 700)
        self.setMinimumSize(640, 480)
        self.setSizeGripEnabled(True)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_header())

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("previewTabs")
        self.tabs.setProperty("uiRole", "segmentedTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_end_roast_tab(), TAB_NAMES[0])
        self.tabs.addTab(self._build_export_tab(), TAB_NAMES[1])
        self.tabs.addTab(self._build_save_batch_tab(), TAB_NAMES[2])
        self.tabs.addTab(self._build_replay_tab(), TAB_NAMES[3])
        self.tabs.addTab(self._build_compare_tab(), TAB_NAMES[4])
        self.tabs.addTab(self._build_config_tab(), TAB_NAMES[5])
        self.tabs.currentChanged.connect(self._sync_confirmation_visibility)
        root.addWidget(self.tabs, 1)

        initial_tab = self._snapshot.get(
            "initial_tab",
            self._confirmation_tab_index if self._confirmation_mode else 0,
        )
        if isinstance(initial_tab, str) and initial_tab in TAB_NAMES:
            initial_tab = TAB_NAMES.index(initial_tab)
        try:
            initial_index = max(0, min(len(TAB_NAMES) - 1, int(initial_tab)))
        except (TypeError, ValueError):
            initial_index = 0
        self.tabs.setCurrentIndex(initial_index)
        root.addWidget(self._build_footer())
        self._sync_confirmation_visibility(self.tabs.currentIndex())

    def snapshot(self) -> dict:
        return deepcopy(self._snapshot)

    def _build_header(self) -> QtWidgets.QFrame:
        header_host = QtWidgets.QFrame()
        header_host.setObjectName("previewHeader")
        header_host.setProperty("uiRole", "dialogHeader")
        header = QtWidgets.QHBoxLayout(header_host)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("统一只读预览")
        title.setObjectName("dialogTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("六类操作集中核对 · 数据仅来自当前预览快照")
        subtitle.setObjectName("previewSubtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        badge = QtWidgets.QLabel("只读 · 未写入任何文件 / 配置 / 状态")
        badge.setObjectName("readOnlyBadge")
        badge.setProperty("uiRole", "statusBadge")
        badge.setProperty("statusTone", "success")
        badge.setAccessibleName("只读状态：未写入任何文件、配置或运行状态")
        header.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        self.header_close_button = QtWidgets.QPushButton("✕ 关闭")
        self.header_close_button.setObjectName("previewHeaderCloseButton")
        self.header_close_button.setProperty("uiRole", "quietButton")
        self.header_close_button.clicked.connect(self.reject)
        header.addWidget(self.header_close_button)
        return header_host

    def _build_footer(self) -> QtWidgets.QFrame:
        footer_host = QtWidgets.QFrame()
        footer_host.setObjectName("previewFooter")
        footer_host.setProperty("uiRole", "dialogActions")
        footer = QtWidgets.QHBoxLayout(footer_host)
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(8)
        self.footer_note = QtWidgets.QLabel()
        self.footer_note.setObjectName("previewFooterNote")
        self.footer_note.setProperty("uiRole", "secondaryText")
        self.footer_note.setWordWrap(True)
        self.footer_note.setText(
            f"仅「{self._confirmation_tab_name}」页签可执行，其余页签为纯只读预览 · 执行与否由您决定"
            if self._confirmation_mode
            else "纯预览模式 · 未写入任何文件、配置与运行状态"
        )
        footer.addWidget(self.footer_note, 1)
        self.close_button = QtWidgets.QPushButton("取消 / 关闭" if self._confirmation_mode else "关闭")
        self.close_button.setObjectName("previewCloseButton")
        self.close_button.setProperty("uiRole", "quietButton")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        batch_number = _text(self._snapshot.get("batch_number", self._snapshot.get("batch_no", "--")))
        default_confirm_text = f"✓ 确认执行 · 结束烘焙并封存 #{batch_number}"
        configured_confirm_text = self._snapshot.get(
            "confirm_button_text",
            self._snapshot.get("confirmation_button_text"),
        )
        self.confirm_button = QtWidgets.QPushButton(_text(configured_confirm_text, default_confirm_text))
        self.confirm_button.setObjectName("previewConfirmButton")
        self.confirm_button.setProperty("uiRole", "primaryButton")
        self.confirm_button.clicked.connect(self._emit_confirmation)
        footer.addWidget(self.confirm_button)
        QtWidgets.QWidget.setTabOrder(self.tabs, self.close_button)
        QtWidgets.QWidget.setTabOrder(self.close_button, self.confirm_button)
        return footer_host

    def _scroll_tab(self, content: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("previewContentScroll")
        scroll.setProperty("uiRole", "contentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _content_widget(self) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        content = QtWidgets.QWidget()
        content.setObjectName("previewTabContent")
        content.setProperty("uiRole", "tabContent")
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(12)
        return content, layout

    def _summary_grid(self, items: Iterable[tuple[str, Any]], columns: int = 4) -> QtWidgets.QWidget:
        host = QtWidgets.QWidget()
        host.setObjectName("summaryGrid")
        grid = QtWidgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, (label_text, value) in enumerate(items):
            card = QtWidgets.QFrame()
            card.setObjectName("summaryCard")
            card.setProperty("uiRole", "summaryCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(4)
            label = QtWidgets.QLabel(label_text)
            label.setObjectName("summaryLabel")
            label.setProperty("uiRole", "fieldLabel")
            value_label = QtWidgets.QLabel(_text(value))
            value_label.setObjectName("summaryValue")
            value_label.setProperty("uiRole", "numericSummary")
            value_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            card.setAccessibleName(f"{label_text}：{_text(value)}")
            card_layout.addWidget(label)
            card_layout.addWidget(value_label)
            grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        return host

    def _section_title(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("sectionTitle")
        label.setProperty("uiRole", "sectionTitle")
        return label

    def _after_note(self, text: Any) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(_text(text, "仅在用户确认执行后才会产生变更。"))
        label.setObjectName("afterExecutionNote")
        label.setProperty("uiRole", "hintPanel")
        label.setWordWrap(True)
        return label

    def _readonly_table(self, headers: Iterable[str], rows: Iterable[Iterable[Any]], object_name: str) -> QtWidgets.QTableWidget:
        header_list = list(headers)
        row_list = [list(row) for row in rows]
        table = QtWidgets.QTableWidget(len(row_list), len(header_list))
        table.setObjectName(object_name)
        table.setProperty("uiRole", "previewTable")
        table.setHorizontalHeaderLabels(header_list)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumHeight(120)
        for row_index, row in enumerate(row_list):
            for column_index in range(len(header_list)):
                item = QtWidgets.QTableWidgetItem(_text(row[column_index] if column_index < len(row) else "", ""))
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, column_index, item)
        return table

    @staticmethod
    def _summary_items(section: Mapping[str, Any], defaults: Iterable[tuple[str, str]]) -> list[tuple[str, Any]]:
        custom = section.get("summary")
        if isinstance(custom, Mapping):
            return [(str(key), value) for key, value in custom.items()]
        if isinstance(custom, (list, tuple)):
            items = []
            for row in custom:
                if isinstance(row, Mapping):
                    items.append((_text(row.get("label"), ""), row.get("value")))
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    items.append((_text(row[0], ""), row[1]))
            if items:
                return items
        return [(label, section.get(key)) for label, key in defaults]

    def _build_end_roast_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "end_roast", "结束烘焙")
        content, layout = self._content_widget()
        if section.get("available", True) is False:
            empty = QtWidgets.QLabel("当前无进行中的锅次")
            empty.setObjectName("emptyState")
            empty.setProperty("uiRole", "emptyState")
            empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
            return self._scroll_tab(content)
        defaults = (
            ("锅次", "batch"), ("本锅时长", "duration"), ("DROP 时刻", "drop_time"), ("事件", "event_count"),
            ("采样点", "sample_count"), ("曲线", "curve_count"), ("预计落库", "estimated_size"), ("写入目标", "target"),
        )
        layout.addWidget(self._summary_grid(self._summary_items(section, defaults)))
        layout.addWidget(self._section_title("BT 豆温曲线只读缩略图"))
        layout.addWidget(_SparklineWidget(section.get("bt_curve", section.get("curve", []))))
        layout.addWidget(self._section_title("已记录事件"))
        event_rows = []
        for event in section.get("events", []) or []:
            if isinstance(event, Mapping):
                event_rows.append((event.get("event_type", event.get("code")), event.get("time", event.get("time_s")), event.get("bt")))
            else:
                event_rows.append(event)
        if event_rows:
            layout.addWidget(self._readonly_table(("事件代码", "时刻", "豆温 °C"), event_rows, "endRoastEventsTable"))
        else:
            empty_events = QtWidgets.QLabel("暂无（投豆等事件未记录，将按无标记封存）")
            empty_events.setObjectName("emptyState")
            empty_events.setProperty("uiRole", "emptyState")
            layout.addWidget(empty_events)
        layout.addWidget(self._after_note(section.get("after")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _build_export_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "export_curve", "导出曲线")
        content, layout = self._content_widget()
        defaults = (("文件", "file"), ("总行数", "row_count"), ("大小", "size"), ("列数", "column_count"))
        layout.addWidget(self._summary_grid(self._summary_items(section, defaults)))
        layout.addWidget(self._section_title("CSV 前 7 行预览"))
        raw_rows = list(section.get("rows", []) or [])[:7]
        rows = []
        for row in raw_rows:
            if isinstance(row, Mapping):
                rows.append([row.get(header, "") for header in CSV_HEADERS])
            else:
                rows.append(row)
        layout.addWidget(self._readonly_table(CSV_HEADERS, rows, "csvPreviewTable"))
        layout.addWidget(self._after_note(section.get("after")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _build_save_batch_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "save_batch", "保存批次")
        content, layout = self._content_widget()
        layout.addWidget(self._section_title("批次档案字段"))
        fields = section.get("fields", section)
        rows = [(str(key), value) for key, value in fields.items() if key not in {"after", "summary"}] if isinstance(fields, Mapping) else []
        layout.addWidget(self._readonly_table(("字段", "值"), rows, "batchFieldsTable"))
        layout.addWidget(self._after_note(section.get("after")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _build_replay_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "replay", "回放")
        content, layout = self._content_widget()
        defaults = (("回放对象", "batch"), ("时长", "duration"), ("数据点", "point_count"), ("模式", "mode"))
        layout.addWidget(self._summary_grid(self._summary_items(section, defaults)))
        layout.addWidget(self._section_title("回放时间轴"))
        events = list(section.get("events", []) or [])
        layout.addWidget(_TimelineWidget(events, float(section.get("duration_s", 720.0) or 720.0)))
        legend_text = " · ".join(f"{_text(item.get('event_type', item.get('code')))} {_text(item.get('time', item.get('time_s')))}" for item in events if isinstance(item, Mapping))
        legend = QtWidgets.QLabel(legend_text or "暂无事件刻度")
        legend.setObjectName("timelineLegend")
        legend.setProperty("uiRole", "secondaryText")
        legend.setWordWrap(True)
        layout.addWidget(legend)
        layout.addWidget(self._after_note(section.get("after", "执行后可进入 1x / 2x / 4x 只读回放，游标可拖动。")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _build_compare_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "compare", "锅次对比")
        content, layout = self._content_widget()
        if section.get("available", True) is False:
            empty = QtWidgets.QLabel("至少封存一锅并开启下一锅后，可预览两锅对比效果")
            empty.setObjectName("emptyState")
            empty.setProperty("uiRole", "emptyState")
            empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
            return self._scroll_tab(content)
        defaults = (("对比", "batches"), ("峰值 BT", "peak_bt"), ("差值", "difference"), ("叠加", "curve_count"))
        layout.addWidget(self._summary_grid(self._summary_items(section, defaults)))
        layout.addWidget(self._section_title("BT 豆温叠加缩略图"))
        curves = section.get("curves", [section.get("previous_curve", []), section.get("current_curve", [])])
        layout.addWidget(_SparklineWidget(curves))
        layout.addWidget(self._after_note(section.get("after")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _build_config_tab(self) -> QtWidgets.QWidget:
        section = _section(self._snapshot, "config_changes", "配置变更")
        content, layout = self._content_widget()
        layout.addWidget(self._section_title("配置差异"))
        raw_rows = section.get("rows", section.get("changes", []))
        rows = []
        for row in raw_rows or []:
            if isinstance(row, Mapping):
                rows.append((row.get("item", row.get("name")), row.get("current"), row.get("new")))
            else:
                rows.append(row)
        table = self._readonly_table(("配置项", "当前值", "新值"), rows, "configChangesTable")
        for row_index in range(table.rowCount()):
            item = table.item(row_index, 2)
            if item is not None:
                item.setData(QtCore.Qt.ItemDataRole.UserRole, "accent")
        layout.addWidget(table)
        layout.addWidget(self._after_note(section.get("after")))
        layout.addStretch()
        return self._scroll_tab(content)

    def _sync_confirmation_visibility(self, tab_index: int) -> None:
        if hasattr(self, "confirm_button"):
            self.confirm_button.setVisible(
                self._confirmation_mode and tab_index == self._confirmation_tab_index
            )

    def _emit_confirmation(self) -> None:
        if self._confirmation_mode and self.tabs.currentIndex() == self._confirmation_tab_index:
            self.confirm_requested.emit(self.snapshot())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
