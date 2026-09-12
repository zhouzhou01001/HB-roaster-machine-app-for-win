from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.roast.workflow import RoastSessionBuffer, RoastWorkflow


def _seconds(value: float | int | None) -> str:
    total = max(0, int(round(float(value or 0))))
    return f"{total // 60:02d}:{total % 60:02d}"


def _event_time(events: Sequence[Mapping[str, Any]], event_type: str) -> float | None:
    for event in events:
        if str(event.get("event_type", "")).upper() == event_type:
            return float(event.get("time_s") or 0.0)
    return None


def build_end_roast_preview(
    workflow: RoastWorkflow,
    buffer: RoastSessionBuffer,
    batch_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an in-memory-only confirmation preview for the active batch."""
    snapshot = buffer.snapshot()
    samples = snapshot["samples"]
    events = snapshot["events"]
    actions = snapshot["actions"]
    duration_s = float(samples[-1].get("time_s") or 0.0) if samples else 0.0
    drop_s = _event_time(events, "DROP")
    latest_bt = samples[-1].get("bt") if samples else None
    metadata = deepcopy(dict(batch_metadata or {}))
    return {
        "kind": "end_roast",
        "read_only": True,
        "batch_number": workflow.batch_number,
        "stage": workflow.stage.value,
        "summary": {
            "batch": f"锅次 #{workflow.batch_number}",
            "duration": _seconds(duration_s),
            "drop_time": "--" if drop_s is None else _seconds(drop_s),
            "events": f"{len(events)} / 8",
            "samples": len(samples),
            "curves": 11,
            "estimated_size_kb": round((len(samples) * 280 + len(events) * 160 + len(actions) * 120) / 1024, 1),
            "target": "charges",
            "drop_bt": latest_bt,
        },
        "metadata": metadata,
        "snapshot": snapshot,
    }


def build_export_preview(workflow: RoastWorkflow, buffer: RoastSessionBuffer) -> dict[str, Any]:
    snapshot = buffer.snapshot()
    rows: list[dict[str, Any]] = []
    actions = snapshot["actions"]
    for sample in snapshot["samples"]:
        time_s = float(sample.get("time_s") or 0.0)
        action = next((item for item in reversed(actions) if float(item.get("time_s") or 0.0) <= time_s), {})
        rows.append(
            {
                "time_s": int(round(time_s)),
                "ET_C": sample.get("et"),
                "BT_C": sample.get("bt"),
                "IT_C": sample.get("it"),
                "XT_C": sample.get("work", sample.get("xt")),
                "GAS_mbar": float(action.get("gas_mbar", action.get("gas_kpa", 0.0) * 10.0) or 0.0),
                "DAM": action.get("damper_level", 0),
                "SPD": action.get("rpm_level", 0),
            }
        )
    return {
        "kind": "export_curve",
        "read_only": True,
        "batch_number": workflow.batch_number,
        "file_name": f"batch_{workflow.batch_number:04d}.csv",
        "columns": ("time_s", "ET_C", "BT_C", "IT_C", "XT_C", "GAS_mbar", "DAM", "SPD"),
        "rows": rows,
        "preview_rows": deepcopy(rows[:7]),
    }


def build_config_diff_preview(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    changes = [
        {"key": key, "current": current.get(key), "proposed": value}
        for key, value in proposed.items()
        if current.get(key) != value
    ]
    return {"kind": "config_change", "read_only": True, "changes": deepcopy(changes)}
