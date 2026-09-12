from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from app.roast.importer import restore_imported_sample


CURVE_COLORS = {
    "bt": "#ff8c2f",
    "et": "#e8544f",
    "ror": "#ff8c2f",
    "background_dark": "#8a8d93",
    "background_light": "#9a968c",
    "inlet": "#4fb3ff",
    "exhaust": "#d7a84b",
    "gas": "#f5d442",
    "damper": "#35c98e",
    "rpm": "#ec6ba9",
}


class _PanOnlyViewBox(pg.ViewBox):
    """Allow deliberate left-button panning while rejecting zoom gestures."""

    def wheelEvent(self, event, axis=None) -> None:
        event.accept()

    def mouseDragEvent(self, event, axis=None) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            super().mouseDragEvent(event, axis=axis)
            return
        event.accept()


class _WheelLockedAxisItem(pg.AxisItem):
    """Keep axis dragging intact but prevent wheel events reaching a ViewBox."""

    def wheelEvent(self, event) -> None:
        event.accept()

    def tickStrings(self, values, scale, spacing):
        labels = super().tickStrings(values, scale, spacing)
        if len(values) < 2 or not labels:
            return labels
        horizontal = self.orientation in ("bottom", "top")
        length = float(self.width() if horizontal else self.height())
        span = abs(float(self.range[1]) - float(self.range[0]))
        if length <= 0 or span <= 0:
            return labels
        font = self.style.get("tickFont") or QtGui.QFont("Microsoft YaHei UI", 12)
        metrics = QtGui.QFontMetricsF(font)
        previous_end = float("-inf")
        # Suppress only text, not ticks: manual spacing and export stay intact.
        visible = [""] * len(labels)
        positioned = []
        for index, (value, label) in enumerate(zip(values, labels)):
            if not label:
                continue
            position = (float(value) - float(self.range[0])) * length / span
            extent = metrics.horizontalAdvance(label) if horizontal else metrics.height()
            positioned.append((position, extent, index, label))
        for position, extent, index, label in sorted(positioned):
            start = position - extent / 2.0
            if start >= previous_end + 12.0:
                visible[index] = label
                previous_end = position + extent / 2.0
        return visible


class CurveWidget(QtWidgets.QWidget):
    """One synchronized roast chart: temperatures on the left axis, RoR on the right."""

    time_range_changed = QtCore.Signal(float, float)
    line_width_changed = QtCore.Signal(float)
    axis_range_changed = QtCore.Signal(str, float, float, float)
    axis_auto_changed = QtCore.Signal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("curveWidget")
        self.setProperty("uiRole", "unifiedRoastChart")
        self.setAccessibleName("烘焙曲线双泳道图")
        self.setAccessibleDescription("上泳道显示温度与升温率，下泳道显示燃气、风门和滚筒转速记录")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        primary_view = _PanOnlyViewBox(enableMenu=False)
        primary_view.setMouseMode(pg.ViewBox.PanMode)
        axis_items = {
            "bottom": _WheelLockedAxisItem(orientation="bottom"),
            "left": _WheelLockedAxisItem(orientation="left"),
            "right": _WheelLockedAxisItem(orientation="right"),
        }
        self.temp_plot = pg.PlotWidget(
            title=None,
            viewBox=primary_view,
            axisItems=axis_items,
        )
        self.temp_plot.setObjectName("unifiedRoastPlot")
        self.temp_plot.setProperty("uiRole", "roastPlot")
        self.temp_plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.temp_plot.setBackground("#11161d")
        self.temp_plot.setStyleSheet(
            "QGraphicsView {"
            " background-color: #11161d;"
            " border: 1px solid #2c3543;"
            " border-radius: 8px;"
            "}"
        )
        layout.addWidget(self.temp_plot)
        self.plot_item = self.temp_plot.getPlotItem()
        self.plot_item.showGrid(x=True, y=True, alpha=0.13)
        self.plot_item.setMenuEnabled(False)
        self.plot_item.setLabel("bottom", "烘焙时间", units="s", size="11pt")
        self.plot_item.setLabel("left", "温度 °C / 工艺参数（操作归一化 %）", size="11pt")
        self.plot_item.showAxis("right")
        self.plot_item.setLabel("right", "升温率 RoR", units="°C/min", size="11pt")
        self.plot_item.vb.setLimits(xMin=0, yMin=0)
        self.legend = self.plot_item.addLegend(offset=(10, 10))
        self.legend.setVisible(False)
        self.ror_view = _PanOnlyViewBox(enableMenu=False)
        self.ror_view.setMouseMode(pg.ViewBox.PanMode)
        self.ror_view.setLimits(yMin=0)
        self.plot_item.scene().addItem(self.ror_view)
        self.plot_item.getAxis("right").linkToView(self.ror_view)
        # Both layers occupy one plot: avoid pixel-alignment offsets from
        # pyqtgraph's link intended for separate plots with different geometry.
        self.ror_view.setMouseEnabled(x=False, y=True)
        self.plot_item.vb.sigResized.connect(self._sync_ror_view)
        self._sync_ror_view()

        self.it_curve = self.temp_plot.plot(pen=pg.mkPen(CURVE_COLORS["inlet"], width=2), name="进风温 IT")
        self.et_curve = self.temp_plot.plot(pen=pg.mkPen(CURVE_COLORS["et"], width=2), name="炉温 ET")
        self.bt_curve = self.temp_plot.plot(pen=pg.mkPen(CURVE_COLORS["bt"], width=3), name="豆温 BT")
        self.exhaust_curve = self.temp_plot.plot(
            pen=pg.mkPen(CURVE_COLORS["exhaust"], width=2),
            name="排风温 XT",
        )
        self.bg_bt_curve = self.temp_plot.plot(pen=pg.mkPen(CURVE_COLORS["background_dark"], width=2, style=QtCore.Qt.PenStyle.DashLine), name="背景 BT")
        self.it_ror_curve = self._add_ror_curve(CURVE_COLORS["inlet"], 1, "IT RoR", QtCore.Qt.PenStyle.DashLine)
        self.et_ror_curve = self._add_ror_curve(CURVE_COLORS["et"], 1, "ET RoR", QtCore.Qt.PenStyle.DashLine)
        self.bt_ror_curve = self._add_ror_curve(CURVE_COLORS["bt"], 2, "BT RoR", QtCore.Qt.PenStyle.DashLine)
        self.exhaust_ror_curve = self._add_ror_curve(CURVE_COLORS["exhaust"], 1, "XT RoR", QtCore.Qt.PenStyle.DashLine)
        self.bg_ror_curve = self._add_ror_curve(CURVE_COLORS["background_dark"], 1, "背景 RoR", QtCore.Qt.PenStyle.DashLine)
        self.bt_pred_curve = self._add_ror_curve(CURVE_COLORS["ror"], 2, "BT RoR 预测", QtCore.Qt.PenStyle.DashLine)
        self.gas_curve = self.temp_plot.plot(
            pen=pg.mkPen(CURVE_COLORS["gas"], width=2),
            name="燃气（归一化）",
        )
        self.damper_curve = self.temp_plot.plot(
            pen=pg.mkPen(CURVE_COLORS["damper"], width=2),
            name="风门档位（归一化）",
        )
        self.rpm_curve = self.temp_plot.plot(
            pen=pg.mkPen(CURVE_COLORS["rpm"], width=2),
            name="转速档位（归一化）",
        )
        self._curve_items = {
            "IT": self.it_curve,
            "ET": self.et_curve,
            "BT": self.bt_curve,
            "EXHAUST": self.exhaust_curve,
            "IT_ROR": self.it_ror_curve,
            "ET_ROR": self.et_ror_curve,
            "BT_ROR": self.bt_ror_curve,
            "EXHAUST_ROR": self.exhaust_ror_curve,
            "PRED_ROR": self.bt_pred_curve,
            "GAS": self.gas_curve,
            "DAMPER": self.damper_curve,
            "RPM": self.rpm_curve,
            "BG_BT": self.bg_bt_curve,
            "BG_ROR": self.bg_ror_curve,
        }
        self._curve_groups = {
            "IT": (self.it_curve,),
            "ET": (self.et_curve,),
            "BT": (self.bt_curve,),
            "EXHAUST": (self.exhaust_curve,),
            "IT_ROR": (self.it_ror_curve,),
            "ET_ROR": (self.et_ror_curve,),
            "BT_ROR": (self.bt_ror_curve,),
            "EXHAUST_ROR": (self.exhaust_ror_curve,),
            "PRED_ROR": (self.bt_pred_curve,),
            "GAS": (self.gas_curve,),
            "DAMPER": (self.damper_curve,),
            "RPM": (self.rpm_curve,),
            "BG_BT": (self.bg_bt_curve,),
            "BG_ROR": (self.bg_ror_curve,),
        }
        self._curve_labels = {
            "BT": "豆温 BT",
            "ET": "炉温 ET",
            "IT": "进风温 IT",
            "EXHAUST": "排风温 XT",
            "BT_ROR": "豆温 RoR",
            "EXHAUST_ROR": "排风温 RoR",
            "ET_ROR": "炉温 RoR",
            "IT_ROR": "进风温 RoR",
            "PRED_ROR": "预测 RoR",
            "GAS": "燃气（归一化）",
            "DAMPER": "风门档位（0-10→归一化）",
            "RPM": "转速档位（0-10→归一化）",
            "BG_BT": "背景 BT",
            "BG_ROR": "背景 RoR",
        }
        self._line_specs = {
            "IT": (CURVE_COLORS["inlet"], 2, QtCore.Qt.PenStyle.SolidLine),
            "ET": (CURVE_COLORS["et"], 2, QtCore.Qt.PenStyle.SolidLine),
            "BT": (CURVE_COLORS["bt"], 3, QtCore.Qt.PenStyle.SolidLine),
            "EXHAUST": (CURVE_COLORS["exhaust"], 2, QtCore.Qt.PenStyle.SolidLine),
            "IT_ROR": (CURVE_COLORS["inlet"], 1, QtCore.Qt.PenStyle.DashLine),
            "ET_ROR": (CURVE_COLORS["et"], 1, QtCore.Qt.PenStyle.DashLine),
            "BT_ROR": (CURVE_COLORS["bt"], 2, QtCore.Qt.PenStyle.DashLine),
            "EXHAUST_ROR": (CURVE_COLORS["exhaust"], 1, QtCore.Qt.PenStyle.DashLine),
            "PRED_ROR": (CURVE_COLORS["bt"], 2, QtCore.Qt.PenStyle.DashLine),
            "GAS": (CURVE_COLORS["gas"], 2, QtCore.Qt.PenStyle.SolidLine),
            "DAMPER": (CURVE_COLORS["damper"], 2, QtCore.Qt.PenStyle.SolidLine),
            "RPM": (CURVE_COLORS["rpm"], 2, QtCore.Qt.PenStyle.SolidLine),
            "BG_BT": (CURVE_COLORS["background_dark"], 2, QtCore.Qt.PenStyle.DashLine),
            "BG_ROR": (CURVE_COLORS["background_dark"], 1, QtCore.Qt.PenStyle.DashLine),
        }
        self._curve_visibility = {key: True for key in self._curve_items}
        self._line_width_scale = 1.0
        self._curve_width_scales = {key: 1.0 for key in self._curve_items}
        self._background_has_rows = False
        self._last_times = []
        self._event_markers = []
        self._target_markers = []
        self._event_specs = []
        self._target_specs = []
        self.auto_follow = True
        self._axis_settings = {
            "x": {"min": 0.0, "max": 600.0, "major": 60.0},
            "left": {"min": 0.0, "max": 400.0, "major": 50.0},
            "right": {"min": 0.0, "max": 20.0, "major": 5.0},
        }
        self._axis_auto = {"x": True, "left": False, "right": True}
        self._default_x_range = (0.0, 600.0)
        self._view_update_depth = 0
        self.lane_separator = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen("#3c4554", width=1, style=QtCore.Qt.PenStyle.DashLine),
        )
        self.lane_separator.setZValue(-5)
        self.temp_plot.addItem(self.lane_separator)
        lane_font = QtGui.QFont("Microsoft YaHei UI", 10)
        self.temperature_lane_label = pg.TextItem(
            "温度 / RoR（左轴 °C · 右轴 °C/min）",
            color="#8f98a8",
            anchor=(0, 0),
        )
        self.temperature_lane_label.setFont(lane_font)
        self.process_lane_label = pg.TextItem(
            "工艺参数 · 风门/转速 0–10（图内归一化）",
            color="#8f98a8",
            anchor=(0, 1),
        )
        self.process_lane_label.setFont(lane_font)
        self.temperature_lane_label.setZValue(20)
        self.process_lane_label.setZValue(20)
        self.temp_plot.addItem(self.temperature_lane_label)
        self.temp_plot.addItem(self.process_lane_label)
        self.plot_item.vb.sigXRangeChanged.connect(self._on_x_range_changed)
        self.plot_item.vb.sigRangeChanged.connect(self._sync_lane_guides)
        manual_signal = getattr(self.plot_item.vb, "sigRangeChangedManually", None)
        if manual_signal is not None:
            manual_signal.connect(self._on_manual_view_changed)
        right_manual_signal = getattr(self.ror_view, "sigRangeChangedManually", None)
        if right_manual_signal is not None:
            right_manual_signal.connect(self._on_manual_view_changed)
        self.set_background_profile([])
        self.set_theme(True)
        self.apply_view_settings(12, 1.0)
        self._apply_tick_spacing()
        self._set_view_range("x", *self._default_x_range)
        self._set_view_range(
            "left",
            self._axis_settings["left"]["min"],
            self._axis_settings["left"]["max"],
        )
        self._set_view_range(
            "right",
            self._axis_settings["right"]["min"],
            self._axis_settings["right"]["max"],
        )
        self._sync_lane_guides()

        # Axis labels already identify both units; keep the data area clear.
        self.temperature_lane_label.hide()
        self.temp_plot.setToolTip(
            "实线：温度；同色虚线：RoR。左轴：温度 / 归一化工艺参数；右轴：RoR。\n"
            "左键拖动查看细节，滚轮不缩放；使用上方坐标和曲线显示调整视图。"
        )

    def _add_ror_curve(self, color: str, width: int, name: str, style=QtCore.Qt.PenStyle.SolidLine):
        curve = pg.PlotDataItem(pen=pg.mkPen(color, width=width, style=style))
        self.ror_view.addItem(curve)
        self.legend.addItem(curve, name)
        return curve

    def _sync_ror_view(self) -> None:
        self.ror_view.setGeometry(self.plot_item.vb.sceneBoundingRect())
        self.ror_view.setXRange(*self.plot_item.vb.viewRange()[0], padding=0)

    def _sync_lane_guides(self, *_args) -> None:
        x_range, y_range = self.plot_item.vb.viewRange()
        x_span = max(1.0, float(x_range[1]) - float(x_range[0]))
        y_span = max(1.0, float(y_range[1]) - float(y_range[0]))
        separator_y = float(y_range[0]) + y_span * 0.28
        label_x = float(x_range[0]) + x_span * 0.018
        self.lane_separator.setPos(separator_y)
        self.temperature_lane_label.setPos(label_x, float(y_range[1]) - y_span * 0.035)
        self.process_lane_label.setPos(label_x, separator_y - y_span * 0.025)

    def _on_x_range_changed(self, _view, time_range) -> None:
        if len(time_range) >= 2:
            left, right = float(time_range[0]), float(time_range[1])
            self.ror_view.setXRange(left, right, padding=0)
            self._axis_settings["x"].update(min=left, max=right)
            self.time_range_changed.emit(left, right)

    def _on_manual_view_changed(self, view, axes) -> None:
        if self._view_update_depth:
            return
        if isinstance(axes, (tuple, list)) and len(axes) == 2 and all(isinstance(item, bool) for item in axes):
            axes = [index for index, changed in enumerate(axes) if changed]
        else:
            axes = list(axes) if axes is not None else [0, 1]
        if not axes:
            axes = [0, 1]
        if 0 in axes:
            # A deliberate left drag may inspect older data temporarily. Keep
            # live follow enabled so the next sample restores the latest point.
            self.axis_auto_changed.emit("x", self._axis_auto["x"])
        if 1 in axes:
            axis = "right" if view is self.ror_view else "left"
            self._axis_auto[axis] = False
            self.axis_auto_changed.emit(axis, False)

    def _set_view_range(self, axis: str, minimum: float, maximum: float) -> None:
        if axis in ("left", "right"):
            minimum = max(0.0, float(minimum))
            maximum = max(float(maximum), minimum + 0.001)
        self._view_update_depth += 1
        try:
            if axis == "x":
                self.temp_plot.setXRange(minimum, maximum, padding=0)
            elif axis == "left":
                self.plot_item.vb.setYRange(minimum, maximum, padding=0)
            elif axis == "right":
                self.ror_view.setYRange(minimum, maximum, padding=0)
        finally:
            self._view_update_depth -= 1

    def _apply_tick_spacing(self) -> None:
        for axis_name, axis_item_name in (("x", "bottom"), ("left", "left"), ("right", "right")):
            major = self._axis_settings[axis_name]["major"]
            axis_item = self.plot_item.getAxis(axis_item_name)
            axis_item.setTickSpacing(major=major, minor=max(major / 5.0, 0.001))

    @staticmethod
    def _nice_tick_step(span: float, target_intervals: int) -> float:
        if not np.isfinite(span) or span <= 0:
            return 1.0
        raw = span / max(1, int(target_intervals))
        exponent = float(np.floor(np.log10(raw)))
        magnitude = 10.0 ** exponent
        fraction = raw / magnitude
        for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
            if fraction <= candidate:
                return candidate * magnitude
        return 10.0 * magnitude

    def _target_tick_intervals(self, axis: str) -> int:
        if axis == "x":
            pixels = max(360.0, float(self.plot_item.vb.width()))
            return max(4, min(12, int(pixels // 90.0)))
        pixels = max(240.0, float(self.plot_item.vb.height()))
        return max(4, min(10, int(pixels // 55.0)))

    def _update_auto_tick_spacing(self, axis: str) -> None:
        minimum, maximum = self._range_for_axis(axis)
        major = self._nice_tick_step(
            maximum - minimum,
            self._target_tick_intervals(axis),
        )
        self._axis_settings[axis]["major"] = major
        axis_item_name = {"x": "bottom", "left": "left", "right": "right"}[axis]
        self.plot_item.getAxis(axis_item_name).setTickSpacing(
            major=major,
            minor=max(major / 5.0, 0.001),
        )

    @staticmethod
    def _validated_axis_values(axis: str, values: Mapping) -> dict:
        try:
            minimum = float(values["min"])
            maximum = float(values["max"])
            major = float(values["major"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{axis} 轴范围必须是数字") from exc
        if not all(np.isfinite(value) for value in (minimum, maximum, major)):
            raise ValueError(f"{axis} 轴范围不能是非有限值")
        if maximum <= minimum:
            raise ValueError(f"{axis} 轴最大值必须大于最小值")
        if major <= 0:
            raise ValueError(f"{axis} 轴主刻度必须大于 0")
        if axis == "x" and (minimum < 0 or maximum > 99999.0):
            raise ValueError("X 轴时间必须在 0 至 99999 秒之间")
        if axis != "x":
            minimum = max(0.0, minimum)
        if axis != "x" and maximum > 10000.0:
            raise ValueError(f"{axis} 轴范围超出允许边界")
        if maximum <= minimum:
            raise ValueError(f"{axis} 轴最大值必须大于最小值")
        return {"min": minimum, "max": maximum, "major": major}

    def _range_for_axis(self, axis: str) -> tuple[float, float]:
        if axis == "x":
            values = self.plot_item.vb.viewRange()[0]
        elif axis == "left":
            values = self.plot_item.vb.viewRange()[1]
        else:
            values = self.ror_view.viewRange()[1]
        return float(values[0]), float(values[1])

    def axis_settings(self) -> Mapping[str, Mapping[str, float]]:
        result = {}
        for axis, values in self._axis_settings.items():
            minimum, maximum = self._range_for_axis(axis)
            result[axis] = {
                "min": minimum,
                "max": maximum,
                "span": maximum - minimum,
                "major": float(values["major"]),
            }
        return result

    def axis_auto_states(self) -> Mapping[str, bool]:
        return dict(self._axis_auto)

    def _visible_values(self, axis: str) -> list[float]:
        left, right = self.time_range()
        values = []
        for key, item in self._curve_items.items():
            if not self._curve_visibility.get(key, True):
                continue
            if key in ("BG_BT", "BG_ROR") and not self._background_has_rows:
                continue
            item_axis = "right" if item.getViewBox() is self.ror_view else "left"
            if item_axis != axis:
                continue
            x_data, y_data = item.getData()
            if x_data is None or y_data is None:
                continue
            selected = []
            for x_value, y_value in zip(x_data, y_data):
                x_number = self._number(x_value)
                y_number = self._number(y_value)
                if np.isfinite(x_number) and np.isfinite(y_number) and left <= x_number <= right:
                    selected.append(y_number)
            if not selected:
                selected = [self._number(value) for value in y_data if np.isfinite(self._number(value))]
            values.extend(selected)
        return values

    @staticmethod
    def _fit_range(values: list[float]) -> tuple[float, float] | None:
        if not values:
            return None
        minimum, maximum = min(values), max(values)
        span = maximum - minimum
        padding = max(1.0, span * 0.08)
        if span == 0:
            padding = max(1.0, abs(minimum) * 0.08)
        return minimum - padding, maximum + padding

    def _fit_x_range(self) -> None:
        if not self._last_times:
            return
        latest = max(self._last_times)
        left = self._default_x_range[0]
        minimum_right = self._default_x_range[1]
        headroom = max(30.0, latest * 0.03)
        proposed_right = max(minimum_right, latest + headroom)
        major = self._nice_tick_step(
            proposed_right - left,
            self._target_tick_intervals("x"),
        )
        right = float(np.ceil(proposed_right / major) * major)
        self._axis_settings["x"].update(min=left, max=right)
        self._set_view_range("x", left, right)
        self._update_auto_tick_spacing("x")

    def _fit_y_ranges(self) -> None:
        for axis in ("left", "right"):
            if not self._axis_auto[axis]:
                continue
            fitted = self._fit_range(self._visible_values(axis))
            if fitted is None:
                continue
            minimum, maximum = fitted
            major = max(0.001, float(self._axis_settings[axis]["major"]))
            minimum = max(0.0, float(np.floor(minimum / major) * major))
            maximum = float(np.ceil(maximum / major) * major)
            if maximum <= minimum:
                maximum = minimum + major
            self._axis_settings[axis].update(min=minimum, max=maximum)
            self._set_view_range(axis, minimum, maximum)
            self._update_auto_tick_spacing(axis)

    def apply_axis_settings(self, settings: Mapping[str, Mapping]) -> None:
        normalized = {}
        auto_states = {}
        for axis in ("x", "left", "right"):
            values = settings.get(axis)
            if values is None:
                raise ValueError(f"缺少 {axis} 轴设置")
            normalized[axis] = self._validated_axis_values(axis, values)
            auto_states[axis] = bool(values.get("auto", False))

        self._axis_settings.update(normalized)
        self._axis_auto.update(auto_states)
        self.auto_follow = auto_states["x"]
        self._apply_tick_spacing()
        for axis in ("x", "left", "right"):
            if self._axis_auto[axis]:
                if axis == "x":
                    self._fit_x_range()
                    if not self._last_times:
                        self._set_view_range("x", normalized[axis]["min"], normalized[axis]["max"])
                elif self._visible_values(axis):
                    self._fit_y_ranges()
                else:
                    self._set_view_range(axis, normalized[axis]["min"], normalized[axis]["max"])
            else:
                self._set_view_range(axis, normalized[axis]["min"], normalized[axis]["max"])
            actual_min, actual_max = self._range_for_axis(axis)
            self.axis_range_changed.emit(axis, actual_min, actual_max, normalized[axis]["major"])
            self.axis_auto_changed.emit(axis, self._axis_auto[axis])

    def set_axis_range(self, axis: str, minimum: float, maximum: float, major: float | None = None, auto: bool = False) -> None:
        axis = {"left_y": "left", "right_y": "right"}.get(axis, axis)
        if axis not in self._axis_settings:
            raise KeyError(axis)
        settings = {key: dict(value) for key, value in self._axis_settings.items()}
        for key, enabled in self._axis_auto.items():
            settings[key]["auto"] = enabled
        settings[axis].update(min=minimum, max=maximum, auto=auto)
        if major is not None:
            settings[axis]["major"] = major
        self.apply_axis_settings(settings)

    def axis_range(self, axis: str) -> tuple[float, float, float]:
        axis = {"left_y": "left", "right_y": "right"}.get(axis, axis)
        if axis not in self._axis_settings:
            raise KeyError(axis)
        values = self.axis_settings()[axis]
        return float(values["min"]), float(values["max"]), float(values["major"])

    @property
    def x_major_tick(self) -> float:
        return float(self._axis_settings["x"]["major"])

    @property
    def left_y_major_tick(self) -> float:
        return float(self._axis_settings["left"]["major"])

    @property
    def right_y_major_tick(self) -> float:
        return float(self._axis_settings["right"]["major"])

    @staticmethod
    def _number(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(np.nan)
        return number if np.isfinite(number) else float(np.nan)

    def _series(self, rows, key: str):
        return [self._number(row.get(key)) for row in rows]

    def _set_sample_series(self, item, rows, times, key: str, fallback: str | None = None) -> None:
        values = self._series(rows, key)
        if fallback is not None:
            values = [value if np.isfinite(value) else self._number(row.get(fallback))
                      for row, value in zip(rows, values)]
        finite = np.isfinite(times) & np.isfinite(values)
        connect = np.zeros(len(rows), dtype=bool)
        if len(rows) > 1:
            # pyqtgraph connect[i] joins point i to i+1, so a gap BEFORE
            # the next sample disables the current edge, not the next edge.
            connect[:-1] = finite[:-1] & finite[1:] & np.array(
                [not bool(row.get("gap_before", False)) for row in rows[1:]], dtype=bool,
            )
        item.setData(times, values, connect=connect)

    def set_data(self, samples, prediction=None, events=None) -> None:
        samples = [restore_imported_sample(sample) for sample in samples]
        times = self._series(samples, "time_s")
        self._last_times = [value for value in times if np.isfinite(value)]
        for item, key, fallback in (
            (self.it_curve, "it", None),
            (self.et_curve, "et", None),
            (self.bt_curve, "bt", None),
            (self.exhaust_curve, "work", "CH4"),
            (self.it_ror_curve, "it_ror", None),
            (self.et_ror_curve, "et_ror", None),
            (self.bt_ror_curve, "bt_ror", None),
            (self.exhaust_ror_curve, "work_ror", "WORK_RoR"),
        ):
            self._set_sample_series(item, samples, times, key, fallback)
        pred = (prediction or {}).get("BT_RoR") or []
        if pred:
            pred_x, pred_y = zip(*pred)
            self.bt_pred_curve.setData(
                [self._number(value) for value in pred_x],
                [self._number(value) for value in pred_y],
                clear=False,
            )
        else:
            self.bt_pred_curve.setData([], [])
        if events is not None:
            self.set_events(events)
        if self.auto_follow:
            if times:
                self._fit_x_range()
            else:
                self._axis_settings["x"].update(
                    min=self._default_x_range[0],
                    max=self._default_x_range[1],
                )
                self._set_view_range("x", *self._default_x_range)
                self._update_auto_tick_spacing("x")
        self._fit_y_ranges()

    def set_actions(self, actions: Iterable[Mapping]) -> None:
        rows = sorted(actions, key=lambda item: float(item.get("time_s", 0)))
        times = [float(item.get("time_s", 0)) for item in rows]
        self.gas_curve.setData(
            times,
            [min(100.0, max(0.0, float(item.get("gas_kpa") or 0) * 10)) for item in rows],
        )
        self.damper_curve.setData(
            times,
            [self._action_level(item, "damper_level", "damper_pct", 10.0) for item in rows],
        )
        self.rpm_curve.setData(
            times,
            [self._action_level(item, "rpm_level", "rpm", 3.0) for item in rows],
        )
        self._fit_y_ranges()

    @staticmethod
    def _action_level(item: Mapping, level_key: str, legacy_key: str, legacy_divisor: float) -> float:
        raw_level = item.get(level_key)
        if raw_level is not None:
            return min(100.0, max(0.0, float(raw_level) * 10.0))
        raw = float(item.get(legacy_key) or 0.0)
        return min(100.0, max(0.0, raw if legacy_key == "damper_pct" else raw / legacy_divisor))

    def set_time_range(self, left: float, right: float) -> None:
        self.set_axis_range("x", left, right, self._axis_settings["x"]["major"], auto=False)

    def time_range(self) -> tuple[float, float]:
        values = self.plot_item.vb.viewRange()[0]
        return float(values[0]), float(values[1])

    def curve_labels(self) -> Mapping[str, str]:
        return dict(self._curve_labels)

    def set_curve_visibility(self, key: str, visible: bool) -> None:
        if key not in self._curve_groups:
            raise KeyError(key)
        self._curve_visibility[key] = bool(visible)
        for item in self._curve_groups[key]:
            if key in ("BG_BT", "BG_ROR"):
                item_visible = bool(visible) and self._background_has_rows
            else:
                item_visible = bool(visible)
            item.setVisible(item_visible)
            self._set_legend_visibility(item, item_visible)

    def _set_legend_visibility(self, item, visible: bool) -> None:
        for sample, label in getattr(self.legend, "items", []):
            if getattr(sample, "item", None) is item:
                sample.setVisible(bool(visible))
                label.setVisible(bool(visible))

    def curve_visibility(self) -> Mapping[str, bool]:
        return dict(self._curve_visibility)

    def set_line_width(self, scale: float) -> None:
        width_scale = max(0.6, min(1.8, float(scale)))
        self._line_width_scale = width_scale
        for key in self._curve_items:
            self.set_curve_line_width(key, width_scale, emit=False)
        self.line_width_changed.emit(width_scale)

    def set_curve_line_width(self, key: str, scale: float, emit: bool = True) -> None:
        if key not in self._curve_items:
            raise KeyError(key)
        width_scale = max(0.6, min(1.8, float(scale)))
        self._curve_width_scales[key] = width_scale
        color, base_width, style = self._line_specs[key]
        self._curve_items[key].setPen(
            pg.mkPen(color, width=max(1, base_width * width_scale), style=style)
        )
        if emit:
            self.line_width_changed.emit(width_scale)

    def curve_line_width(self, key: str) -> float:
        if key not in self._curve_items:
            raise KeyError(key)
        return self._curve_width_scales[key]

    def line_width(self) -> float:
        return self._line_width_scale

    def set_background_profile(self, samples: Iterable[Mapping], name: str = "") -> None:
        rows = [restore_imported_sample(sample) for sample in samples]
        times = self._series(rows, "time_s")
        self._set_sample_series(self.bg_bt_curve, rows, times, "bt")
        self._set_sample_series(self.bg_ror_curve, rows, times, "bt_ror")
        self._background_has_rows = bool(rows)
        bg_bt_visible = self._curve_visibility["BG_BT"] and self._background_has_rows
        bg_ror_visible = self._curve_visibility["BG_ROR"] and self._background_has_rows
        self.bg_bt_curve.setVisible(bg_bt_visible)
        self.bg_ror_curve.setVisible(bg_ror_visible)
        self._set_legend_visibility(self.bg_bt_curve, bg_bt_visible)
        self._set_legend_visibility(self.bg_ror_curve, bg_ror_visible)
        self._background_name = name
        self._fit_y_ranges()

    def set_targets(self, yellow_c: float, fc_c: float) -> None:
        for marker in self._target_markers:
            self.temp_plot.removeItem(marker)
        self._target_markers = []
        self._target_specs = []
        for value, label, color in ((yellow_c, "目标转黄", "#d7ae52"), (fc_c, "目标一爆", "#c56d53")):
            marker = pg.InfiniteLine(pos=float(value), angle=0, pen=pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DotLine), label=f"{label} {value:.0f} °C", labelOpts={"position": 0.96, "color": color})
            self.temp_plot.addItem(marker)
            self._target_markers.append(marker)
            self._target_specs.append({"value": float(value), "label": label, "color": color})

    def set_auto_follow(self, enabled: bool) -> None:
        self.auto_follow = bool(enabled)
        self._axis_auto["x"] = self.auto_follow
        self.axis_auto_changed.emit("x", self.auto_follow)
        if self.auto_follow and self._last_times:
            self._fit_x_range()

    def set_axis_auto(self, axis: str, enabled: bool) -> None:
        if axis not in self._axis_auto:
            raise KeyError(axis)
        enabled = bool(enabled)
        if axis == "x":
            self.set_auto_follow(enabled)
            return
        self._axis_auto[axis] = enabled
        if enabled:
            self._fit_y_ranges()
        self.axis_auto_changed.emit(axis, enabled)

    def set_theme(self, dark: bool) -> None:
        background = "#11161d" if dark else "#f4f1ea"
        foreground = "#e9edf2" if dark else "#303944"
        frame = "#2c3543" if dark else "#c9c2b6"
        background_curve = CURVE_COLORS["background_dark" if dark else "background_light"]
        self.temp_plot.setBackground(background)
        self.temp_plot.setStyleSheet(
            "QGraphicsView {"
            f" background-color: {background};"
            f" border: 1px solid {frame};"
            " border-radius: 8px;"
            "}"
        )
        self.plot_item.showGrid(x=True, y=True, alpha=0.13 if dark else 0.16)
        self.plot_item.setTitle(None)
        for axis in ("left", "bottom", "right"):
            self.plot_item.getAxis(axis).setPen(foreground)
            self.plot_item.getAxis(axis).setTextPen(foreground)
        for key in ("BG_BT", "BG_ROR"):
            _, base_width, style = self._line_specs[key]
            self._line_specs[key] = (background_curve, base_width, style)
            self._curve_items[key].setPen(
                pg.mkPen(
                    background_curve,
                    width=max(1, base_width * self._curve_width_scales[key]),
                    style=style,
                )
            )

    def apply_view_settings(self, font_size: int, curve_scale: float) -> None:
        safe_font_size = max(10, min(22, int(font_size)))
        safe_scale = max(0.8, min(1.4, float(curve_scale)))
        font = QtGui.QFont("Microsoft YaHei UI", safe_font_size)
        self.plot_item.titleLabel.item.setFont(font)
        for axis in ("left", "bottom", "right"):
            self.plot_item.getAxis(axis).setStyle(
                tickFont=font, maxTextLevel=0, hideOverlappingLabels=True,
            )
            self.plot_item.getAxis(axis).label.setFont(font)
        font_factor = max(0.9, min(1.35, safe_font_size / 12.0))
        minimum_height = max(260, min(340, int(260 * safe_scale * font_factor)))
        maximum_height = max(minimum_height + 90, min(760, int(760 * safe_scale * font_factor)))
        self.setMinimumHeight(minimum_height)
        self.setMaximumHeight(maximum_height)
        self.temp_plot.setMinimumHeight(0)
        self.temp_plot.setMaximumHeight(16777215)

    def set_events(self, events) -> None:
        for marker in self._event_markers:
            self.temp_plot.removeItem(marker)
        self._event_markers = []
        self._event_specs = []
        labels = {"TP": "回温点", "CHARGE": "下豆", "YELLOW": "转黄", "FC_START": "一爆开始", "FC_END": "一爆结束", "SC_START": "二爆开始", "SC_END": "二爆结束", "DROP": "排豆"}
        for event in events:
            marker = pg.InfiniteLine(pos=float(event.get("time_s", 0.0)), angle=90, pen=pg.mkPen("#9e7c48", width=1, style=QtCore.Qt.PenStyle.DashLine), label=labels.get(str(event.get("event_type", "")), "事件"), labelOpts={"position": 0.92, "color": "#d8b36b"})
            self.temp_plot.addItem(marker)
            self._event_markers.append(marker)
            self._event_specs.append({"time_s": float(event.get("time_s", 0.0)), "label": labels.get(str(event.get("event_type", "")), "事件")})

    def export_snapshot(self) -> dict:
        curves = []
        for key, item in self._curve_items.items():
            visible = bool(self._curve_visibility.get(key, True))
            if key in ("BG_BT", "BG_ROR"):
                visible = visible and self._background_has_rows
            x_data, y_data = item.getData()
            points = []
            connect = []
            if x_data is not None and y_data is not None:
                indices = [
                    index for index, (x, y) in enumerate(zip(x_data, y_data))
                    if np.isfinite(self._number(x)) and np.isfinite(self._number(y))
                ]
                points = [(float(x_data[index]), float(y_data[index])) for index in indices]
                mask = item.opts.get("connect")
                connect = [
                    following == current + 1 and (
                        bool(mask[current]) if isinstance(mask, np.ndarray) else True
                    )
                    for current, following in zip(indices, indices[1:])
                ]
                if points:
                    connect.append(False)
            color, _, style = self._line_specs[key]
            curves.append({
                "key": key,
                "label": self._curve_labels[key],
                "axis": "right" if item.getViewBox() is self.ror_view else "left",
                "visible": visible,
                "points": points,
                "connect": connect,
                "color": color,
                "width": float(item.opts["pen"].widthF()),
                "dashed": style == QtCore.Qt.PenStyle.DashLine,
            })
        axis = self.axis_settings()
        return {
            "title": "HB 烘焙统一图表",
            "x_range": (axis["x"]["min"], axis["x"]["max"]),
            "left_range": (axis["left"]["min"], axis["left"]["max"]),
            "right_range": (axis["right"]["min"], axis["right"]["max"]),
            "x_major": axis["x"]["major"],
            "left_major": axis["left"]["major"],
            "right_major": axis["right"]["major"],
            "axis_labels": {"x": "烘焙时间 (s)", "left": "温度 (°C) / 工艺参数（操作归一化 %）", "right": "升温率 RoR (°C/min)"},
            "curves": curves,
            "events": list(self._event_specs),
            "targets": list(self._target_specs),
            "background_name": self._background_name,
        }
