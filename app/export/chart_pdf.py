from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Mapping

from PySide6 import QtCore, QtGui


def _pdf_font_family() -> str:
    """Offscreen Qt on Windows may not enumerate installed fonts automatically."""
    families = QtGui.QFontDatabase.families()
    if not families and os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc"):
            path = fonts / name
            if path.is_file():
                QtGui.QFontDatabase.addApplicationFont(str(path))
        families = QtGui.QFontDatabase.families()
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC"):
        if family in families:
            return family
    if families:
        return families[0]
    raise RuntimeError("PDF 导出没有可用字体，请安装系统字体后重试")


def _ticks(minimum: float, maximum: float, step: float) -> list[float]:
    if step <= 0 or not math.isfinite(step):
        return []
    value = math.ceil(minimum / step) * step
    values = []
    while value <= maximum + step * 0.001 and len(values) < 100:
        values.append(value)
        value += step
    return values


def _number(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 100 or float(value).is_integer() else f"{value:.1f}"


def _map(value: float, minimum: float, maximum: float, start: float, length: float) -> float:
    if maximum <= minimum:
        return start
    return start + (value - minimum) / (maximum - minimum) * length


def _draw_series(painter: QtGui.QPainter, points, x_range, y_range, rect, color: str, width: float, dashed: bool, connect=None) -> None:
    if len(points) < 2:
        return
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(max(0.8, width * 0.75))
    if dashed:
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
    painter.setPen(pen)
    path = QtGui.QPainterPath()
    started = False
    for index, (x_value, y_value) in enumerate(points):
        if connect is not None and index > 0 and not connect[index - 1]:
            started = False
        x = _map(x_value, x_range[0], x_range[1], rect.left(), rect.width())
        y = rect.bottom() - _map(y_value, y_range[0], y_range[1], 0.0, rect.height())
        if not math.isfinite(x) or not math.isfinite(y):
            started = False
            continue
        if not started:
            path.moveTo(x, y)
            started = True
        else:
            path.lineTo(x, y)
    painter.drawPath(path)


def export_chart_pdf(target: str | Path, chart, title: str = "HB 烘焙统一图表") -> None:
    """用 Qt 矢量绘制当前图表快照，避免截图导出裁切和 DPI 依赖。"""

    snapshot: Mapping = chart.export_snapshot()
    font_family = _pdf_font_family()
    target = Path(target)
    writer = QtGui.QPdfWriter(str(target))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
    writer.setPageOrientation(QtGui.QPageLayout.Orientation.Landscape)
    writer.setResolution(144)
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Cannot write chart PDF: {target}")
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    page = QtCore.QRectF(0, 0, writer.width(), writer.height())
    painter.fillRect(page, QtGui.QColor("white"))
    painter.setPen(QtGui.QColor("#1d2b25"))
    painter.setFont(QtGui.QFont(font_family, 16, QtGui.QFont.Weight.Bold))
    painter.drawText(QtCore.QRectF(48, 18, page.width() - 96, 45), QtCore.Qt.AlignmentFlag.AlignLeft, title)
    painter.setFont(QtGui.QFont(font_family, 8))
    painter.drawText(QtCore.QRectF(48, 68, page.width() - 96, 24), QtCore.Qt.AlignmentFlag.AlignLeft, f"X {_number(snapshot['x_range'][0])}-{_number(snapshot['x_range'][1])} s   |   左 Y {_number(snapshot['left_range'][0])}-{_number(snapshot['left_range'][1])}   |   右 Y {_number(snapshot['right_range'][0])}-{_number(snapshot['right_range'][1])}")
    plot = QtCore.QRectF(78, 105, page.width() - 170, page.height() - 245)
    painter.setPen(QtGui.QPen(QtGui.QColor("#d9dfda"), 0.8))
    for value in _ticks(snapshot["x_range"][0], snapshot["x_range"][1], snapshot["x_major"]):
        x = _map(value, *snapshot["x_range"], plot.left(), plot.width())
        painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
    for value in _ticks(snapshot["left_range"][0], snapshot["left_range"][1], snapshot["left_major"]):
        y = plot.bottom() - _map(value, *snapshot["left_range"], 0.0, plot.height())
        painter.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))
    painter.setPen(QtGui.QPen(QtGui.QColor("#26332d"), 1.2))
    painter.drawRect(plot)
    painter.save()
    painter.setClipRect(plot)
    for event in snapshot["events"]:
        x = _map(event["time_s"], *snapshot["x_range"], plot.left(), plot.width())
        painter.setPen(QtGui.QPen(QtGui.QColor("#a47a37"), 1.0, QtCore.Qt.PenStyle.DashLine))
        painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
        painter.drawText(QtCore.QPointF(x + 3, plot.top() + 16), event["label"])
    for target_spec in snapshot["targets"]:
        y = plot.bottom() - _map(target_spec["value"], *snapshot["left_range"], 0.0, plot.height())
        painter.setPen(QtGui.QPen(QtGui.QColor(target_spec["color"]), 1.0, QtCore.Qt.PenStyle.DotLine))
        painter.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))
        painter.drawText(QtCore.QRectF(plot.left() + 4, y - 25, plot.width() - 8, 22), QtCore.Qt.AlignmentFlag.AlignRight, f"{target_spec['label']} {target_spec['value']:.0f} °C")
    for curve in snapshot["curves"]:
        if curve["visible"]:
            y_range = snapshot["right_range"] if curve["axis"] == "right" else snapshot["left_range"]
            _draw_series(painter, curve["points"], snapshot["x_range"], y_range, plot, curve["color"], curve["width"], curve["dashed"], connect=curve.get("connect"))
    painter.restore()
    painter.setFont(QtGui.QFont(font_family, 8))
    painter.setPen(QtGui.QColor("#26332d"))
    for value in _ticks(snapshot["x_range"][0], snapshot["x_range"][1], snapshot["x_major"]):
        x = _map(value, *snapshot["x_range"], plot.left(), plot.width())
        painter.drawText(QtCore.QRectF(x - 22, plot.bottom() + 6, 44, 16), QtCore.Qt.AlignmentFlag.AlignCenter, _number(value))
    for value in _ticks(snapshot["left_range"][0], snapshot["left_range"][1], snapshot["left_major"]):
        y = plot.bottom() - _map(value, *snapshot["left_range"], 0.0, plot.height())
        painter.drawText(QtCore.QRectF(8, y - 8, 62, 16), QtCore.Qt.AlignmentFlag.AlignRight, _number(value))
    for value in _ticks(snapshot["right_range"][0], snapshot["right_range"][1], snapshot["right_major"]):
        y = plot.bottom() - _map(value, *snapshot["right_range"], 0.0, plot.height())
        painter.drawText(QtCore.QRectF(plot.right() + 8, y - 8, 62, 16), QtCore.Qt.AlignmentFlag.AlignLeft, _number(value))
    painter.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 28, plot.width(), 18), QtCore.Qt.AlignmentFlag.AlignCenter, snapshot["axis_labels"]["x"])
    painter.save()
    painter.translate(25, plot.bottom())
    painter.rotate(-90)
    painter.drawText(QtCore.QRectF(0, 0, plot.height(), 18), QtCore.Qt.AlignmentFlag.AlignCenter, snapshot["axis_labels"]["left"])
    painter.restore()
    painter.save()
    painter.translate(page.width() - 22, plot.bottom())
    painter.rotate(-90)
    painter.drawText(QtCore.QRectF(0, 0, plot.height(), 18), QtCore.Qt.AlignmentFlag.AlignCenter, snapshot["axis_labels"]["right"])
    painter.restore()
    legend_y = plot.bottom() + 55
    x = plot.left()
    painter.setFont(QtGui.QFont(font_family, 7))
    metrics = painter.fontMetrics()
    legend_height = max(18, metrics.height() + 2)
    for curve in snapshot["curves"]:
        if not curve["visible"]:
            continue
        label_width = metrics.horizontalAdvance(curve["label"]) + 4
        item_width = 22 + label_width + 16
        if x + item_width > plot.right() and x > plot.left():
            x = plot.left()
            legend_y += legend_height
        pen = QtGui.QPen(QtGui.QColor(curve["color"]), max(1.0, curve["width"] * 0.7))
        if curve["dashed"]:
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(x, legend_y + 6), QtCore.QPointF(x + 18, legend_y + 6))
        painter.setPen(QtGui.QColor("#26332d"))
        painter.drawText(QtCore.QRectF(x + 22, legend_y, label_width, legend_height), QtCore.Qt.AlignmentFlag.AlignLeft, curve["label"])
        x += item_width
    painter.setPen(QtGui.QColor("#526158"))
    painter.drawText(QtCore.QRectF(48, page.height() - 38, page.width() - 96, 18), QtCore.Qt.AlignmentFlag.AlignLeft, "事件线、目标线、背景线与当前可见曲线均按当前视图范围导出。")
    if not painter.end():
        raise OSError(f"Cannot finish chart PDF: {target}")
