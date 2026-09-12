from __future__ import annotations

from typing import Any, List

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from app.database.database import RoastDatabase


def _safe_rows(db: RoastDatabase, method_name: str) -> list[dict]:
    method = getattr(db, method_name, None)
    if not callable(method):
        return []
    return [dict(row) for row in method()]


def _archive_replay_data(
    db: RoastDatabase,
    batch_id: int,
) -> tuple[dict, list[dict], list[dict], list[dict]] | None:
    load_batch = getattr(db, "load_batch", None)
    load_charge = getattr(db, "load_charge", None)
    if not callable(load_batch) or not callable(load_charge):
        return None
    batch = load_batch(batch_id)
    if not batch:
        return None
    charge = load_charge(int(batch["charge_id"]))
    if not charge:
        return dict(batch), [], [], []
    return (
        dict(batch),
        [dict(item) for item in charge.get("samples", [])],
        [dict(item) for item in charge.get("events", [])],
        [dict(item) for item in charge.get("actions", [])],
    )


def _legacy_replay_data(
    db: RoastDatabase,
    roast_id: int,
) -> tuple[dict, list[dict], list[dict], list[dict]] | None:
    roast = next(
        (row for row in _safe_rows(db, "list_roasts") if int(row.get("id", -1)) == roast_id),
        None,
    )
    if roast is None:
        return None
    load_actions = getattr(db, "load_manual_actions", None)
    actions = load_actions(roast_id) if callable(load_actions) else []
    return (
        roast,
        [dict(item) for item in db.load_samples(roast_id)],
        [dict(item) for item in db.load_events(roast_id)],
        [dict(item) for item in actions],
    )


def _uses_dark_theme(widget: QtWidgets.QWidget) -> bool:
    current = widget.parentWidget()
    while current is not None:
        theme_name = getattr(current, "theme_name", None)
        if theme_name is not None:
            return theme_name == "深色工作台"
        current = current.parentWidget()
    return widget.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128


class ReplayWindow(QtWidgets.QWidget):
    """Replay a recorded roast without communicating with the roaster."""

    def __init__(
        self,
        samples: List[dict] | RoastDatabase,
        events: List[dict] | int | None = None,
        actions: List[dict] | None = None,
        parent=None,
        *,
        source: str = "auto",
    ) -> None:
        archive_record: dict[str, Any] = {}
        source_kind = "memory"
        if isinstance(samples, RoastDatabase):
            db = samples
            requested_id = int(events) if events is not None else None
            if requested_id is None:
                batches = _safe_rows(db, "list_batches")
                requested_id = int(batches[0]["id"]) if batches else -1
                source = "archive"
            normalized_source = str(source).strip().lower()
            if normalized_source not in {"auto", "archive", "legacy"}:
                raise ValueError("source must be 'auto', 'archive', or 'legacy'")
            if normalized_source == "auto":
                legacy_ids = {int(row["id"]) for row in _safe_rows(db, "list_roasts")}
                normalized_source = "legacy" if requested_id in legacy_ids else "archive"
            loaded = (
                _archive_replay_data(db, requested_id)
                if normalized_source == "archive"
                else _legacy_replay_data(db, requested_id)
            )
            archive_record, loaded_samples, loaded_events, loaded_actions = loaded or ({}, [], [], [])
            samples = loaded_samples
            events = loaded_events
            actions = loaded_actions
            source_kind = normalized_source
        elif events is None or actions is None:
            raise TypeError("events and actions are required for in-memory replay")

        super().__init__(parent)
        self.setObjectName("replayWindow")
        self.setProperty("uiRole", "secondaryWindow")
        self.setWindowFlags(QtCore.Qt.WindowType.Window)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("烘焙回放")
        self.resize(920, 680)
        self.setMinimumSize(640, 480)
        self.archive_record = archive_record
        self.source_kind = source_kind
        self.samples = sorted(samples, key=lambda sample: float(sample.get("time_s", 0)))
        self.events = sorted(events, key=lambda item: float(item.get("time_s", 0)))
        self.actions = sorted(actions, key=lambda item: float(item.get("time_s", 0)))
        source_text = {
            "archive": "语义归档 · 只读",
            "legacy": "兼容旧档 · 只读",
            "memory": "当前内存快照 · 只读",
        }[self.source_kind]
        self.index = 0
        self._updating_slider = False
        self._plot_theme_dark: bool | None = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("烘焙回放")
        title.setObjectName("screenTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("沿时间轴复盘温度、升温率、事件与人工操作")
        subtitle.setObjectName("subtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.source_status = QtWidgets.QLabel(source_text)
        self.source_status.setObjectName("replayStatusBadge")
        self.source_status.setProperty("uiRole", "statusBadge")
        self.source_status.setProperty("statusTone", "neutral")
        self.source_status.setAccessibleName(f"回放来源：{source_text}")
        header.addWidget(self.source_status, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.temp_plot = pg.PlotWidget()
        self.ror_plot = pg.PlotWidget()
        self.temp_plot.setAccessibleName("温度回放曲线图")
        self.ror_plot.setAccessibleName("豆温升温率回放曲线图")
        self.temp_plot.setMinimumHeight(180)
        self.ror_plot.setMinimumHeight(130)
        self.chart_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.chart_splitter.setObjectName("chartSplitter")
        self.chart_splitter.setChildrenCollapsible(False)
        for heading, plot in (("温度回放", self.temp_plot), ("豆温升温率回放", self.ror_plot)):
            panel = QtWidgets.QFrame()
            panel.setObjectName("panel")
            panel.setProperty("uiRole", "panel")
            panel_layout = QtWidgets.QVBoxLayout(panel)
            panel_layout.setContentsMargins(12, 8, 12, 12)
            panel_layout.setSpacing(8)
            section_title = QtWidgets.QLabel(heading)
            section_title.setObjectName("sectionTitle")
            section_title.setProperty("uiRole", "sectionTitle")
            panel_layout.addWidget(section_title)
            panel_layout.addWidget(plot, 1)
            self.chart_splitter.addWidget(panel)
            plot.showGrid(x=True, y=True, alpha=0.16)
            plot.addLegend(offset=(8, 8))
            plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.chart_splitter.setStretchFactor(0, 3)
        self.chart_splitter.setStretchFactor(1, 2)
        layout.addWidget(self.chart_splitter, 1)

        self.it_curve = self.temp_plot.plot(name="入风温 IT")
        self.et_curve = self.temp_plot.plot(name="环境温 ET")
        self.bt_curve = self.temp_plot.plot(name="豆温 BT")
        self.ror_curve = self.ror_plot.plot(name="BT 升温率")

        transport_panel = QtWidgets.QFrame()
        transport_panel.setObjectName("panel")
        transport_panel.setProperty("uiRole", "panel")
        self.transport_layout = QtWidgets.QGridLayout(transport_panel)
        self.transport_layout.setContentsMargins(12, 8, 12, 12)
        self.transport_layout.setHorizontalSpacing(8)
        self.transport_layout.setVerticalSpacing(8)
        self.transport_title = QtWidgets.QLabel("回放控制")
        self.transport_title.setObjectName("sectionTitle")
        self.transport_title.setProperty("uiRole", "sectionTitle")
        self.transport_layout.addWidget(self.transport_title, 0, 0, 1, 7)

        self.btn_play = QtWidgets.QPushButton("播放")
        self.btn_play.setObjectName("primaryButton")
        self.btn_play.setProperty("uiRole", "primaryButton")
        self.btn_stop = QtWidgets.QPushButton("暂停")
        self.btn_reset = QtWidgets.QPushButton("重置")
        self.speed = QtWidgets.QComboBox()
        self.speed.addItems(["0.5x", "1x", "2x"])
        self.speed.setCurrentText("1x")
        self.speed.setObjectName("replaySpeedCombo")
        self.speed.setProperty("uiRole", "formField")
        self.speed_label = QtWidgets.QLabel("速度")
        self.speed_label.setObjectName("fieldLabel")
        self.speed_label.setProperty("uiRole", "fieldLabel")
        self.speed_label.setBuddy(self.speed)
        self.btn_close = QtWidgets.QPushButton("关闭")
        self.btn_close.setObjectName("replayCloseButton")
        self.btn_close.setProperty("uiRole", "quietButton")
        self.transport_layout.addWidget(self.btn_play, 1, 0)
        self.transport_layout.addWidget(self.btn_stop, 1, 1)
        self.transport_layout.addWidget(self.btn_reset, 1, 2)
        self.transport_layout.addWidget(self.speed_label, 1, 3)
        self.transport_layout.addWidget(self.speed, 1, 4)
        self.transport_layout.setColumnStretch(5, 1)
        self.transport_layout.addWidget(self.btn_close, 1, 6)

        self.progress = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.progress.setObjectName("timelineSlider")
        self.progress.setProperty("uiRole", "accentSlider")
        self.progress.setRange(0, max(0, len(self.samples)))
        self.progress.setToolTip("拖动定位；获得焦点后可使用左右方向键微调")
        self.progress.setAccessibleName("回放时间轴")
        self.transport_layout.addWidget(self.progress, 2, 0, 1, 7)
        layout.addWidget(transport_panel)

        info_panel = QtWidgets.QFrame()
        info_panel.setObjectName("infoPanel")
        info_panel.setProperty("uiRole", "panelSecondary")
        info_layout = QtWidgets.QVBoxLayout(info_panel)
        info_layout.setContentsMargins(12, 9, 12, 10)
        info_layout.setSpacing(5)
        info_title = QtWidgets.QLabel("回放状态")
        info_title.setObjectName("sectionTitle")
        info_title.setProperty("uiRole", "sectionTitle")
        info_layout.addWidget(info_title)
        self.current_info = QtWidgets.QLabel("时间：-  |  IT：-  ET：-  BT：-  |  BT 升温率：-")
        self.current_info.setObjectName("metricLine")
        self.current_info.setProperty("uiRole", "numericSummary")
        self.event_info = QtWidgets.QLabel("事件：-")
        self.action_info = QtWidgets.QLabel("人工操作：-")
        for label in (self.current_info, self.event_info, self.action_info):
            label.setWordWrap(True)
            info_layout.addWidget(label)
        layout.addWidget(info_panel)

        self.btn_play.setToolTip("开始或继续回放")
        self.btn_stop.setToolTip("暂停在当前位置")
        self.btn_reset.setToolTip("返回回放起点")
        QtWidgets.QWidget.setTabOrder(self.btn_play, self.btn_stop)
        QtWidgets.QWidget.setTabOrder(self.btn_stop, self.btn_reset)
        QtWidgets.QWidget.setTabOrder(self.btn_reset, self.speed)
        QtWidgets.QWidget.setTabOrder(self.speed, self.btn_close)
        QtWidgets.QWidget.setTabOrder(self.btn_close, self.progress)

        self.btn_play.clicked.connect(self._play)
        self.btn_stop.clicked.connect(self.timer.stop)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_close.clicked.connect(self.close)
        self.progress.valueChanged.connect(self._jump)
        self.btn_play.setEnabled(bool(self.samples))
        self.progress.setEnabled(bool(self.samples))
        if not self.samples:
            self.current_info.setText("当前归档没有可回放的采样数据。")
            self.source_status.setText(f"{source_text} · 空数据")
            self.source_status.setProperty("statusTone", "muted")
        self._apply_responsive_layout(self.width())
        self._apply_plot_theme()

    @classmethod
    def from_archive(
        cls,
        db: RoastDatabase,
        batch_id: int | None = None,
        parent=None,
    ) -> "ReplayWindow":
        """Create a read-only replay from a semantic archive batch."""

        return cls(db, batch_id, None, parent, source="archive")

    @classmethod
    def from_database(
        cls,
        db: RoastDatabase,
        batch_id: int | None = None,
        parent=None,
        *,
        source: str = "auto",
    ) -> "ReplayWindow":
        """Create a replay from an archive or a compatible legacy roast."""

        return cls(db, batch_id, None, parent, source=source)

    def _apply_plot_theme(self) -> None:
        dark = _uses_dark_theme(self)
        if self._plot_theme_dark == dark:
            return
        self._plot_theme_dark = dark
        chart_bg = "#141519" if dark else "#17181b"
        axis_text = "#a6a49e" if dark else "#78756d"
        axis_line = "#3d4046" if dark else "#d6d2c8"
        legend_bg = "#323438" if dark else "#efece5"
        for plot in (self.temp_plot, self.ror_plot):
            plot.setBackground(chart_bg)
            plot.getPlotItem().getViewBox().setBorder(pg.mkPen("#000000"))
            for axis_name in ("left", "bottom"):
                axis = plot.getPlotItem().getAxis(axis_name)
                axis.setPen(axis_line)
                axis.setTextPen(axis_text)
            legend = plot.getPlotItem().legend
            if legend is not None:
                legend.setBrush(pg.mkBrush(legend_bg))
                legend.setPen(pg.mkPen(axis_line))
        self.it_curve.setPen(pg.mkPen("#5ba8e0", width=2))
        self.et_curve.setPen(pg.mkPen("#54cbe8", width=2))
        self.bt_curve.setPen(pg.mkPen("#ffa03a", width=3))
        self.ror_curve.setPen(pg.mkPen("#c98bf0", width=2.5))

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.Type.PaletteChange, QtCore.QEvent.Type.StyleChange):
            QtCore.QTimer.singleShot(0, self._apply_plot_theme)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "transport_layout"):
            self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        compact = width < 760
        self.setProperty("layoutMode", "compact" if compact else "wide")
        if compact:
            self.transport_layout.addWidget(self.transport_title, 0, 0, 1, 4)
            self.transport_layout.addWidget(self.btn_play, 1, 0)
            self.transport_layout.addWidget(self.btn_stop, 1, 1)
            self.transport_layout.addWidget(self.btn_reset, 1, 2)
            self.transport_layout.addWidget(self.btn_close, 1, 3)
            self.transport_layout.addWidget(self.speed_label, 2, 0)
            self.transport_layout.addWidget(self.speed, 2, 1)
            self.transport_layout.addWidget(self.progress, 3, 0, 1, 4)
        else:
            self.transport_layout.addWidget(self.transport_title, 0, 0, 1, 7)
            self.transport_layout.addWidget(self.btn_play, 1, 0)
            self.transport_layout.addWidget(self.btn_stop, 1, 1)
            self.transport_layout.addWidget(self.btn_reset, 1, 2)
            self.transport_layout.addWidget(self.speed_label, 1, 3)
            self.transport_layout.addWidget(self.speed, 1, 4)
            self.transport_layout.addWidget(self.btn_close, 1, 6)
            self.transport_layout.addWidget(self.progress, 2, 0, 1, 7)

    def _interval(self) -> int:
        return {"0.5x": 1000, "1x": 500, "2x": 250}[self.speed.currentText()]

    def _play(self) -> None:
        if self.samples:
            self.timer.start(self._interval())

    def _reset(self) -> None:
        self.timer.stop()
        self.index = 0
        self._render(0)

    def _jump(self, value: int) -> None:
        if not self._updating_slider:
            self.index = value
            self._render(value)

    def _tick(self) -> None:
        if self.index >= len(self.samples):
            self.timer.stop()
            return
        self.index += 1
        self._render(self.index)

    def _render(self, count: int) -> None:
        subset = self.samples[:count]
        if not subset:
            self.it_curve.setData([], [])
            self.et_curve.setData([], [])
            self.bt_curve.setData([], [])
            self.ror_curve.setData([], [])
            self.current_info.setText("时间：-  |  IT：-  ET：-  BT：-  |  BT 升温率：-")
            self.event_info.setText("事件：-")
            self.action_info.setText("人工操作：-")
        else:
            times = [item.get("time_s") for item in subset]
            self.it_curve.setData(times, [item.get("it") for item in subset])
            self.et_curve.setData(times, [item.get("et") for item in subset])
            self.bt_curve.setData(times, [item.get("bt") for item in subset])
            self.ror_curve.setData(times, [item.get("bt_ror") for item in subset])
            current = subset[-1]
            now = float(current.get("time_s", 0))
            self.current_info.setText(f"时间：{now:.1f} 秒  |  IT：{current.get('it', 0):.1f}  ET：{current.get('et', 0):.1f}  BT：{current.get('bt', 0):.1f}  |  BT 升温率：{current.get('bt_ror') if current.get('bt_ror') is not None else '-'}")
            labels = {"TP": "回温点", "CHARGE": "下豆", "YELLOW": "转黄", "FC_START": "一爆开始", "FC_END": "一爆结束", "SC_START": "二爆开始", "SC_END": "二爆结束", "DROP": "排豆"}
            event_names = [labels.get(item.get("event_type", ""), "事件") for item in self.events if item.get("time_s", 0) <= now]
            self.event_info.setText("已发生事件：" + ("、".join(event_names) if event_names else "-"))
            latest_action = next((item for item in reversed(self.actions) if item.get("time_s", 0) <= now), None)
            if latest_action:
                damper = latest_action.get("damper_level")
                if damper is None:
                    damper = float(latest_action.get("damper_pct", 0) or 0) / 10.0
                rpm = latest_action.get("rpm_level")
                if rpm is None:
                    rpm = float(latest_action.get("rpm", 0) or 0) / 30.0
                gas_mbar = latest_action.get("gas_mbar")
                if gas_mbar is None:
                    gas_mbar = float(latest_action.get("gas_kpa", 0) or 0) * 10.0
                self.action_info.setText(f"最近人工操作：燃气 {float(gas_mbar):.1f} mbar，风门 {float(damper):.1f} 档，转速 {float(rpm):.1f} 档")
            else:
                self.action_info.setText("人工操作：-")
        self._updating_slider = True
        self.progress.setValue(count)
        self._updating_slider = False

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)
