from __future__ import annotations

import json
from typing import Any, List

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from app.database.database import RoastDatabase
from app.roast.compare import compute_curve_metrics, roast_landmarks


def _safe_rows(db: RoastDatabase, method_name: str) -> list[dict]:
    method = getattr(db, method_name, None)
    if not callable(method):
        return []
    return [dict(row) for row in method()]


def _archive_data(
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


def _legacy_data(
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


class CompareWindow(QtWidgets.QWidget):
    """Compare immutable archive or legacy curves using read-only database APIs."""

    def __init__(
        self,
        current_batch_id: int,
        db: RoastDatabase,
        parent=None,
        *,
        source: str = "auto",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("compareWindow")
        self.setProperty("uiRole", "secondaryWindow")
        self.setWindowFlags(QtCore.Qt.WindowType.Window)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("曲线对比")
        self.resize(920, 680)
        self.setMinimumSize(640, 480)
        self.db = db
        self.current_batch_id = current_batch_id
        self.current_source = self._resolve_current_source(source)
        current_data = self._load_source(self.current_source, current_batch_id)
        self.current_record = current_data[0] if current_data else {}
        self.current_samples = current_data[1] if current_data else []
        self.current_events = current_data[2] if current_data else []
        self._plot_theme_dark: bool | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setSpacing(2)
        title = QtWidgets.QLabel("曲线对比")
        title.setObjectName("screenTitle")
        title.setProperty("uiRole", "screenTitle")
        subtitle = QtWidgets.QLabel("叠加参考曲线或历史批次，核对温度轨迹与发展阶段")
        subtitle.setObjectName("subtitle")
        subtitle.setProperty("uiRole", "secondaryText")
        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack)
        header.addStretch()
        self.status_badge = QtWidgets.QLabel(
            "语义归档 · 只读" if self.current_source == "archive" else "兼容旧档 · 只读"
        )
        self.status_badge.setObjectName("compareStatusBadge")
        self.status_badge.setProperty("uiRole", "statusBadge")
        self.status_badge.setProperty("statusTone", "neutral")
        self.status_badge.setAccessibleName(f"对比来源：{self.status_badge.text()}")
        header.addWidget(self.status_badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        controls_panel = QtWidgets.QFrame()
        controls_panel.setObjectName("panel")
        controls_panel.setProperty("uiRole", "panel")
        self.controls_layout = QtWidgets.QGridLayout(controls_panel)
        self.controls_layout.setContentsMargins(12, 8, 12, 12)
        self.controls_layout.setHorizontalSpacing(8)
        self.controls_layout.setVerticalSpacing(8)
        self.controls_title = QtWidgets.QLabel("对比目标")
        self.controls_title.setObjectName("sectionTitle")
        self.controls_title.setProperty("uiRole", "sectionTitle")
        self.controls_layout.addWidget(self.controls_title, 0, 0, 1, 6)
        self.mode_label = QtWidgets.QLabel("对比来源")
        self.mode_label.setObjectName("fieldLabel")
        self.mode_label.setProperty("uiRole", "fieldLabel")
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setObjectName("compareSourceCombo")
        self.mode_combo.setProperty("uiRole", "formField")
        self.mode_combo.addItems(["参考曲线", "历史批次"])
        self.mode_label.setBuddy(self.mode_combo)
        self.target_label = QtWidgets.QLabel("目标曲线")
        self.target_label.setObjectName("fieldLabel")
        self.target_label.setProperty("uiRole", "fieldLabel")
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.setObjectName("compareTargetCombo")
        self.target_combo.setProperty("uiRole", "formField")
        self.target_label.setBuddy(self.target_combo)
        self.compare_btn = QtWidgets.QPushButton("开始对比")
        self.compare_btn.setObjectName("primaryButton")
        self.compare_btn.setProperty("uiRole", "primaryButton")
        self.close_btn = QtWidgets.QPushButton("关闭")
        self.close_btn.setObjectName("compareCloseButton")
        self.close_btn.setProperty("uiRole", "quietButton")
        self.controls_layout.addWidget(self.mode_label, 1, 0)
        self.controls_layout.addWidget(self.mode_combo, 1, 1)
        self.controls_layout.addWidget(self.target_label, 1, 2)
        self.controls_layout.addWidget(self.target_combo, 1, 3)
        self.controls_layout.addWidget(self.compare_btn, 1, 4)
        self.controls_layout.addWidget(self.close_btn, 1, 5)
        self.controls_layout.setColumnStretch(3, 1)
        layout.addWidget(controls_panel)

        self.temp_chart = pg.PlotWidget()
        self.ror_chart = pg.PlotWidget()
        self.temp_chart.setAccessibleName("豆温与环境温对比曲线图")
        self.ror_chart.setAccessibleName("豆温升温率对比曲线图")
        self.temp_chart.setMinimumHeight(180)
        self.ror_chart.setMinimumHeight(130)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        splitter.setObjectName("chartSplitter")
        splitter.setChildrenCollapsible(False)
        for heading, chart in (("豆温 BT / 环境温 ET 对比", self.temp_chart), ("豆温升温率对比", self.ror_chart)):
            chart_panel = QtWidgets.QFrame()
            chart_panel.setObjectName("panel")
            chart_panel.setProperty("uiRole", "panel")
            chart_layout = QtWidgets.QVBoxLayout(chart_panel)
            chart_layout.setContentsMargins(12, 8, 12, 12)
            chart_layout.setSpacing(8)
            chart_title = QtWidgets.QLabel(heading)
            chart_title.setObjectName("sectionTitle")
            chart_title.setProperty("uiRole", "sectionTitle")
            chart_layout.addWidget(chart_title)
            chart_layout.addWidget(chart, 1)
            splitter.addWidget(chart_panel)
            chart.showGrid(x=True, y=True, alpha=0.16)
            chart.addLegend(offset=(8, 8))
            chart.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.cur_bt = self.temp_chart.plot(name="Current BT")
        self.tar_bt = self.temp_chart.plot(name="Target BT")
        self.cur_et = self.temp_chart.plot(name="Current ET")
        self.tar_et = self.temp_chart.plot(name="Target ET")
        self.cur_ror = self.ror_chart.plot(name="Current BT RoR")
        self.tar_ror = self.ror_chart.plot(name="Target BT RoR")

        metrics_panel = QtWidgets.QFrame()
        metrics_panel.setObjectName("metricsPanel")
        metrics_panel.setProperty("uiRole", "panelSecondary")
        metrics_layout = QtWidgets.QVBoxLayout(metrics_panel)
        metrics_layout.setContentsMargins(12, 9, 12, 10)
        metrics_layout.setSpacing(6)
        metrics_title = QtWidgets.QLabel("对比指标")
        metrics_title.setObjectName("sectionTitle")
        metrics_title.setProperty("uiRole", "sectionTitle")
        metrics_layout.addWidget(metrics_title)
        self.metrics_label = QtWidgets.QLabel("请选择目标曲线后开始对比。")
        self.metrics_label.setObjectName("metricsText")
        self.metrics_label.setProperty("uiRole", "numericSummary")
        self.metrics_label.setWordWrap(True)
        metrics_layout.addWidget(self.metrics_label)
        layout.addWidget(metrics_panel)

        self.mode_combo.setToolTip("选择参考曲线库或历史烘焙批次")
        self.target_combo.setToolTip("选择需要叠加分析的目标曲线")
        QtWidgets.QWidget.setTabOrder(self.mode_combo, self.target_combo)
        QtWidgets.QWidget.setTabOrder(self.target_combo, self.compare_btn)
        QtWidgets.QWidget.setTabOrder(self.compare_btn, self.close_btn)

        self.mode_combo.currentTextChanged.connect(self._refresh_targets)
        self.compare_btn.clicked.connect(self._run_compare)
        self.close_btn.clicked.connect(self.close)
        self._apply_responsive_layout(self.width())
        self._refresh_targets()
        self._apply_plot_theme()

    def _resolve_current_source(self, source: str) -> str:
        normalized = str(source).strip().lower()
        if normalized not in {"auto", "archive", "legacy"}:
            raise ValueError("source must be 'auto', 'archive', or 'legacy'")
        if normalized != "auto":
            return normalized
        legacy_ids = {int(row["id"]) for row in _safe_rows(self.db, "list_roasts")}
        if self.current_batch_id in legacy_ids:
            return "legacy"
        return "archive"

    def _load_source(
        self,
        source: str,
        identifier: int,
    ) -> tuple[dict, list[dict], list[dict], list[dict]] | None:
        if source == "archive":
            return _archive_data(self.db, identifier)
        if source == "legacy":
            return _legacy_data(self.db, identifier)
        return None

    def _apply_plot_theme(self) -> None:
        dark = _uses_dark_theme(self)
        if self._plot_theme_dark == dark:
            return
        self._plot_theme_dark = dark
        chart_bg = "#141519" if dark else "#17181b"
        axis_text = "#a6a49e" if dark else "#78756d"
        axis_line = "#3d4046" if dark else "#d6d2c8"
        legend_bg = "#323438" if dark else "#efece5"
        target_color = "#8a8d93" if dark else "#9a968c"
        for chart in (self.temp_chart, self.ror_chart):
            chart.setBackground(chart_bg)
            chart.getPlotItem().getViewBox().setBorder(pg.mkPen("#000000"))
            for axis_name in ("left", "bottom"):
                axis = chart.getPlotItem().getAxis(axis_name)
                axis.setPen(axis_line)
                axis.setTextPen(axis_text)
            legend = chart.getPlotItem().legend
            if legend is not None:
                legend.setBrush(pg.mkBrush(legend_bg))
                legend.setPen(pg.mkPen(axis_line))
        dashed = QtCore.Qt.PenStyle.DashLine
        self.cur_bt.setPen(pg.mkPen("#ffa03a", width=2.5))
        self.tar_bt.setPen(pg.mkPen(target_color, width=2, style=dashed))
        self.cur_et.setPen(pg.mkPen("#54cbe8", width=2))
        self.tar_et.setPen(pg.mkPen(target_color, width=1.5, style=dashed))
        self.cur_ror.setPen(pg.mkPen("#c98bf0", width=2.5))
        self.tar_ror.setPen(pg.mkPen(target_color, width=2, style=dashed))

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.Type.PaletteChange, QtCore.QEvent.Type.StyleChange):
            QtCore.QTimer.singleShot(0, self._apply_plot_theme)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "controls_layout"):
            self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        compact = width < 760
        self.setProperty("layoutMode", "compact" if compact else "wide")
        if compact:
            self.controls_layout.addWidget(self.controls_title, 0, 0, 1, 4)
            self.controls_layout.addWidget(self.mode_label, 1, 0)
            self.controls_layout.addWidget(self.mode_combo, 1, 1, 1, 3)
            self.controls_layout.addWidget(self.target_label, 2, 0)
            self.controls_layout.addWidget(self.target_combo, 2, 1, 1, 3)
            self.controls_layout.addWidget(self.compare_btn, 3, 2)
            self.controls_layout.addWidget(self.close_btn, 3, 3)
        else:
            self.controls_layout.addWidget(self.controls_title, 0, 0, 1, 6)
            self.controls_layout.addWidget(self.mode_label, 1, 0)
            self.controls_layout.addWidget(self.mode_combo, 1, 1)
            self.controls_layout.addWidget(self.target_label, 1, 2)
            self.controls_layout.addWidget(self.target_combo, 1, 3)
            self.controls_layout.addWidget(self.compare_btn, 1, 4)
            self.controls_layout.addWidget(self.close_btn, 1, 5)

    def _refresh_targets(self) -> None:
        self.target_combo.clear()
        if self.mode_combo.currentText() == "历史批次":
            for batch in _safe_rows(self.db, "list_batches"):
                batch_id = int(batch["id"])
                if self.current_source == "archive" and batch_id == self.current_batch_id:
                    continue
                number = batch.get("batch_number", batch_id)
                name = batch.get("coffee_name") or "未命名批次"
                self.target_combo.addItem(
                    f"归档 #{number} · {name}",
                    ("archive", batch_id),
                )
            for roast in _safe_rows(self.db, "list_roasts"):
                roast_id = int(roast["id"])
                if self.current_source == "legacy" and roast_id == self.current_batch_id:
                    continue
                self.target_combo.addItem(
                    f"旧档 #{roast_id} · {roast.get('coffee_name') or '未命名批次'}",
                    ("legacy", roast_id),
                )
        else:
            for profile in _safe_rows(self.db, "list_profiles"):
                self.target_combo.addItem(f"{profile['id']} · {profile['name']}", ("profile", profile["id"]))
        if not self.target_combo.count():
            self.target_combo.addItem("暂无可对比目标", ("empty", -1))
        target = self.target_combo.currentData()
        self.compare_btn.setEnabled(bool(target and target[1] != -1))
        self.target_combo.setAccessibleName("历史归档或参考曲线")
        self.target_combo.setAccessibleDescription(
            "只读取归档数据，不会修改批次、配置或设备状态"
        )

    def _target_data(self) -> tuple[List[dict], List[dict]]:
        target = self.target_combo.currentData()
        if not target:
            return [], []
        kind, identifier = target
        if kind in {"archive", "legacy"}:
            loaded = self._load_source(kind, int(identifier))
            return (loaded[1], loaded[2]) if loaded else ([], [])
        if kind == "empty":
            return [], []
        try:
            payload = json.loads(self.db.load_profile_payload(identifier) or "{}")
        except json.JSONDecodeError:
            payload = {}
        return payload.get("samples", []), payload.get("events", [])

    def _run_compare(self) -> None:
        if not self.current_samples:
            self.metrics_label.setText("当前曲线没有可用的采样数据。")
            return
        target_samples, target_events = self._target_data()
        if not target_samples:
            self.metrics_label.setText("目标曲线没有可用的采样数据。")
            return
        current_t = [item.get("time_s") for item in self.current_samples]
        target_t = [item.get("time_s") for item in target_samples]
        self.cur_bt.setData(current_t, [item.get("bt") for item in self.current_samples])
        self.tar_bt.setData(target_t, [item.get("bt") for item in target_samples])
        self.cur_et.setData(current_t, [item.get("et") for item in self.current_samples])
        self.tar_et.setData(target_t, [item.get("et") for item in target_samples])
        self.cur_ror.setData(current_t, [item.get("bt_ror") for item in self.current_samples])
        self.tar_ror.setData(target_t, [item.get("bt_ror") for item in target_samples])
        metrics = compute_curve_metrics(self.current_samples, target_samples)
        current, target = roast_landmarks(self.current_events), roast_landmarks(target_events)
        mae = "-" if metrics["mae_bt"] is None else f"{metrics['mae_bt']:.2f} C"
        current_dev = "-" if current["development_ratio"] is None else f"{current['development_ratio']:.1f}%"
        target_dev = "-" if target["development_ratio"] is None else f"{target['development_ratio']:.1f}%"
        self.metrics_label.setText(f"豆温平均绝对偏差：{mae}  |  重叠时长：{metrics['duration']:.1f} 秒\n当前批次：一爆 {current['fc_start'] or '-'} 秒，排豆 {current['drop'] or '-'} 秒，发展比例 {current_dev}\n目标曲线：一爆 {target['fc_start'] or '-'} 秒，排豆 {target['drop'] or '-'} 秒，发展比例 {target_dev}")
