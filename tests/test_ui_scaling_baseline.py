import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RESOLUTIONS = (
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
    (2880, 1620),
    (2880, 1800),
    (3840, 2160),
)

DPI_SCALES = (
    ("100%", "1"),
    ("125%", "1.25"),
    ("150%", "1.5"),
    ("200%", "2"),
)


UI_GEOMETRY_PROBE = r"""
import json
import sys
import tempfile
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.ui.main_window import MainWindow


width = int(sys.argv[1])
height = int(sys.argv[2])
dpi_label = sys.argv[3]
failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def widget_rect_in_window(window, widget):
    pos = widget.mapTo(window, QtCore.QPoint(0, 0))
    return QtCore.QRect(pos, widget.size())


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
workspace = tempfile.TemporaryDirectory()
window = MainWindow(
    db_path=Path(workspace.name) / "ui_scaling.hbroast",
    channel_mapping_path=Path(workspace.name) / "channel.json",
)
window.resize(width, height)
window.show()
app.processEvents()

actual_size = window.size()
check(
    actual_size.width() <= width and actual_size.height() <= height,
    f"window expanded to {actual_size.width()}x{actual_size.height()} at target {width}x{height} / DPI {dpi_label}",
)

window_rect = window.rect()
for name, widget in (
    ("unified chart", window.curve),
    ("event panel", window.event_panel),
    ("manual action panel", window.action_panel),
):
    rect = widget_rect_in_window(window, widget)
    check(widget.isVisible(), f"{name} is not visible")
    check(rect.width() > 0 and rect.height() > 0, f"{name} has empty geometry {rect.getRect()}")
    check(window_rect.intersects(rect), f"{name} is outside the window: {rect.getRect()}")

check(window.curve.temp_plot.height() >= 260, f"main chart is too short: {window.curve.temp_plot.height()}px")
check(window.curve.temp_plot.height() >= 260, f"unified chart plot is too short: {window.curve.temp_plot.height()}px")
min_event_height = 30
check(window.event_panel.height() >= min_event_height, f"event panel is too short: {window.event_panel.height()}px")
check(window.action_panel.height() >= 150, f"manual action panel is too short: {window.action_panel.height()}px")

window._set_session_active(True)
window.workflow.start_roast()
window.workflow.record_event("CHARGE", 0.0, [])
window._sync_workflow_ui()
app.processEvents()
charge_button = window.event_panel._buttons["CHARGE"]
check(charge_button.isVisible(), "CHARGE milestone button is not visible")
check(not charge_button.isEnabled(), "CHARGE milestone button must lock after charging")
for event_type in ("YELLOW", "FC_START", "FC_END", "SC_START", "SC_END"):
    button = window.event_panel._buttons[event_type]
    check(button.isVisible(), f"{event_type} milestone button is not visible")
    check(button.isEnabled(), f"{event_type} milestone button is not enabled")
    check(button.width() + 2 >= button.sizeHint().width(), f"{event_type} milestone button text may be clipped")
    check(button.height() + 2 >= button.sizeHint().height(), f"{event_type} milestone button height is clipped")

drop_button = window.event_panel._buttons["DROP"]
check(drop_button.isVisible(), "DROP button is not visible")
check(drop_button.isEnabled(), "DROP button is not enabled before recording")
check(drop_button.width() + 2 >= drop_button.sizeHint().width(), "DROP button text may be clipped")

check(window.action_panel.isVisible(), "manual action panel is not visible")
check(window.action_panel.isEnabled(), "manual action panel is not enabled during monitoring")
check(window.action_panel.add_btn.isVisible(), "manual action record button is not visible")
check(window.action_panel.add_btn.isEnabled(), "manual action record button is not enabled")

toolbars = window.findChildren(QtWidgets.QToolBar)
check(bool(toolbars), "toolbar is missing")
for toolbar in toolbars:
    check(not toolbar.isVisible(), "legacy toolbar should be hidden by default")
check(window.act_toggle_legacy_toolbar.isVisible(), "legacy toolbar toggle command is missing")

splitters = window.findChildren(QtWidgets.QSplitter)
check(bool(splitters), "main splitter is missing")
if splitters:
    splitter = splitters[0]
    original_sizes = splitter.sizes()
    splitter.setSizes([1, 999999])
    app.processEvents()
    check(window.curve.temp_plot.height() >= 260, "main chart collapsed after splitter drag")
    check(window.curve.temp_plot.height() >= 260, "unified chart collapsed after splitter drag")
    check(window.event_panel.width() >= window._ui_value(220), "event panel became unusably narrow after splitter drag")
    splitter.setSizes(original_sizes)
    app.processEvents()
    restored_sizes = splitter.sizes()
    check(all(size > 0 for size in restored_sizes), f"splitter did not restore usable sizes: {restored_sizes}")

window.resize(max(640, width - 160), max(480, height - 120))
app.processEvents()
window.resize(width, height)
app.processEvents()
check(window.curve.temp_plot.height() >= 260, "main chart did not recover after resize")
check(window.curve.temp_plot.height() >= 260, "unified chart did not recover after resize")

payload = {
    "target": [width, height],
    "dpi": dpi_label,
    "actual": [actual_size.width(), actual_size.height()],
    "failures": failures,
}
print(json.dumps(payload, ensure_ascii=False))
window.close()
workspace.cleanup()
sys.exit(1 if failures else 0)
"""


class UiScalingBaselineTests(unittest.TestCase):
    def test_resolution_and_dpi_geometry_matrix(self) -> None:
        for width, height in RESOLUTIONS:
            for dpi_label, scale_factor in DPI_SCALES:
                with self.subTest(resolution=f"{width}x{height}", dpi=dpi_label):
                    env = os.environ.copy()
                    env["QT_QPA_PLATFORM"] = "offscreen"
                    env["QT_SCALE_FACTOR"] = scale_factor
                    result = subprocess.run(
                        [sys.executable, "-c", UI_GEOMETRY_PROBE, str(width), str(height), dpi_label],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"UI geometry baseline failed for {width}x{height} at {dpi_label}\n"
                        f"stdout:\n{textwrap.indent(result.stdout, '  ')}\n"
                        f"stderr:\n{textwrap.indent(result.stderr, '  ')}",
                    )


if __name__ == "__main__":
    unittest.main()
