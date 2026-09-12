from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import re
from datetime import datetime
from typing import List


_workspace_issues: List[str] = []


APP_NAME = "烘焙软件"
_FALLBACK_MARKER = "[Fallback]"


class WorkspacePathError(RuntimeError):
    """Raised when a workspace path is not usable as a directory."""


def clear_workspace_issues() -> None:
    """Clear initialization issue cache for tests and deterministic startup."""
    _workspace_issues.clear()


def get_workspace_issues() -> tuple[str, ...]:
    """Return collected workspace initialization issues."""
    return tuple(_workspace_issues)


def _register_issue(message: str) -> None:
    if message not in _workspace_issues:
        _workspace_issues.append(message)


def _ensure_dir(path: Path, label: str) -> None:
    for candidate in (path, *path.parents):
        if candidate.exists() and not candidate.is_dir():
            raise WorkspacePathError(f"{label} 目标存在同名文件：{candidate}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise WorkspacePathError(f"{label} 创建失败，路径与文件冲突：{path}") from exc
    except PermissionError as exc:
        raise WorkspacePathError(f"{label} 缺少写入权限：{path}") from exc
    except OSError as exc:
        raise WorkspacePathError(f"{label} 创建失败：{path}（{exc}）") from exc


def _portable_root() -> Path:
    """
    Return an OS-appropriate writable base directory for app settings and data.
    This path remains stable between source and executable runs.
    """
    if sys.platform.startswith("win"):
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _candidate_workspace_roots() -> list[Path]:
    if sys.platform.startswith("win"):
        candidates: list[Path] = []
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / APP_NAME)
        else:
            candidates.append(Path.home() / "AppData" / "Roaming" / APP_NAME)

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "RoastMonitor" / APP_NAME)
        else:
            candidates.append(Path.home() / "AppData" / "Local" / "RoastMonitor" / APP_NAME)

        candidates.append(Path(tempfile.gettempdir()) / "RoastMonitor" / APP_NAME)
        return candidates

    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / APP_NAME]

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return [Path(xdg) / APP_NAME]
    return [Path.home() / ".local" / "share" / APP_NAME]


def ensure_workspace_dirs() -> tuple[Path, Path]:
    """
    Ensure and return (data_dir, config_dir).
    """
    # Keep startup diagnostics across default-path lookups until explicitly reset.
    roots = _candidate_workspace_roots()
    failed_roots: list[str] = []
    last_error = ""

    for root in roots:
        data_dir = root / "data"
        config_dir = root / "config"
        try:
            _ensure_dir(data_dir, f"工作区数据目录 {root}")
            _ensure_dir(config_dir, f"工作区配置目录 {root}")
            return data_dir, config_dir
        except WorkspacePathError as exc:
            last_error = f"{_FALLBACK_MARKER} {root}：{exc}"
            failed_roots.append(last_error)
            _register_issue(last_error)
            continue

    raise RuntimeError(
        "工作区目录初始化失败：未找到可写目录；已按顺序尝试 "
        + " -> ".join(str(item) for item in [str(root) for root in roots])
        + f"；失败原因：{'; '.join(failed_roots)}"
    ) from WorkspacePathError(last_error or "无可用目录")


def default_db_path() -> Path:
    data_dir, _ = ensure_workspace_dirs()
    return data_dir / "default.hbroast"


def default_channel_mapping_path() -> Path:
    _, config_dir = ensure_workspace_dirs()
    return config_dir / "channel.json"


def default_debug_log_dir() -> Path:
    data_dir, _ = ensure_workspace_dirs()
    debug_dir = data_dir / "debug_logs"
    _ensure_dir(debug_dir, "调试日志目录")
    return debug_dir


def default_debug_log_path(
    source: str = "",
    batch_id: int | None = None,
    profile: str = "",
) -> Path:
    debug_dir = default_debug_log_dir()
    source_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", source.strip() or "unknown")
    profile_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", profile.strip() or "unknown")
    batch_tag = f"_batch{int(batch_id)}" if batch_id is not None else ""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"roast_debug_{timestamp}_{source_tag}_{profile_tag}{batch_tag}.jsonl"
    return debug_dir / filename
