from __future__ import annotations

import sys
import json
import os
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        raise SystemExit(
            "缺少运行依赖：PySide6。请先执行 `python -m pip install -r requirements.txt` "
            "（确保与当前运行脚本的 Python 解释器一致）后再启动。"
        )
    raise

from app.ui.main_window import MainWindow
from app.utils.paths import default_db_path, default_channel_mapping_path, get_workspace_issues
from app.version import APP_VERSION


def _write_startup_probe(window, target: str, nonce: str) -> None:
    if not window.isVisible() or not window.curve.temp_plot.isVisible():
        return
    if window.curve.temp_plot.width() <= 0 or window.curve.temp_plot.height() <= 0:
        return
    path = Path(target)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "status": "GUI_READY", "nonce": nonce, "product_version": APP_VERSION,
        "process_id": os.getpid(), "qt_version": QtCore.qVersion(),
        "window_visible": True, "chart_visible": True,
        "window_size": [window.width(), window.height()],
    }), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    try:
        db_path = default_db_path()
        channel_mapping_path = default_channel_mapping_path()
    except RuntimeError as exc:
        QtWidgets.QMessageBox.critical(None, "工作区初始化失败", f"{exc}")
        return 1
    window = MainWindow(
        db_path=db_path,
        channel_mapping_path=channel_mapping_path,
    )
    warnings = [item for item in get_workspace_issues() if item]
    if warnings:
        QtWidgets.QMessageBox.warning(
            window,
            "数据目录初始化提醒",
            "检测到数据目录异常，已降级到可用路径：\n" + "\n".join(warnings),
        )
    window.show()
    probe_path = os.environ.get("HB_STARTUP_PROBE_PATH")
    probe_nonce = os.environ.get("HB_STARTUP_PROBE_NONCE")
    if probe_path and probe_nonce:
        QtCore.QTimer.singleShot(
            250, lambda: _write_startup_probe(window, probe_path, probe_nonce),
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
