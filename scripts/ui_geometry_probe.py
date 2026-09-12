from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6 import QtWidgets

from app.ui.main_window import MainWindow


RESOLUTIONS = (
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
    (2880, 1620),
    (2880, 1800),
    (3840, 2160),
)


def _size(size) -> dict[str, int]:
    return {"width": int(size.width()), "height": int(size.height())}


def _widget_geometry(widget) -> dict[str, int]:
    rect = widget.geometry()
    return {"x": rect.x(), "y": rect.y(), "width": rect.width(), "height": rect.height()}


def _fits(widget) -> bool:
    rect = widget.geometry()
    return rect.width() > 0 and rect.height() > 0


def probe(width: int, height: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="hb-ui-probe-") as temp_dir:
        root = Path(temp_dir)
        window = MainWindow(
            db_path=root / "probe.hbroast",
            channel_mapping_path=root / "channel.json",
        )
        window.event_panel.set_events([{"event_type": "YELLOW", "time_s": 202.0}])
        window.resize(width, height)
        window.show()
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        toolbar = window.findChild(QtWidgets.QToolBar)
        event_buttons = {
            kind: {
                "actual": _widget_geometry(button),
                "size_hint": _size(button.sizeHint()),
                "fits": _fits(button),
            }
            for kind, button in window.event_panel._buttons.items()
        }
        result = {
            "scale_factor": os.environ.get("QT_SCALE_FACTOR", "system"),
            "requested_window": {"width": width, "height": height},
            "actual_window": _size(window.size()),
            "minimum_size_hint": _size(window.minimumSizeHint()),
            "toolbar": {
                "actual": _widget_geometry(toolbar),
                "size_hint": _size(toolbar.sizeHint()),
            },
            "splitter_sizes": [int(value) for value in window.panel_splitter.sizes()],
            "right_control_width": int(window.right_host.width()),
            "buttons": event_buttons,
            "plot_area": {
                "unified": _size(window.curve.temp_plot.viewport().size()),
            },
        }
        result["clipped_or_invalid"] = (
            result["actual_window"]["width"] <= 0
            or result["actual_window"]["height"] <= 0
            or result["minimum_size_hint"]["width"] > width
            or result["minimum_size_hint"]["height"] > height
            or result["toolbar"]["size_hint"]["width"] > result["actual_window"]["width"]
            or not all(item["fits"] for item in event_buttons.values())
            or any(value <= 0 for value in result["splitter_sizes"])
            or any(value <= 0 for value in result["plot_area"]["unified"].values())
        )
        window.close()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="HB-Roast-Monitor Qt 分辨率/DPI 几何探针")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    args = parser.parse_args()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    resolutions = ((args.width, args.height),) if args.width and args.height else RESOLUTIONS
    for width, height in resolutions:
        print(json.dumps(probe(width, height), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
