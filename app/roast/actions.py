from __future__ import annotations

from typing import Dict


def normalize_action(item: Dict) -> Dict:
    damper = item.get("damper_level")
    if damper is None:
        damper = float(item.get("damper_pct") or 0.0) / 10.0
    rpm = item.get("rpm_level")
    if rpm is None:
        rpm = float(item.get("rpm") or 0.0) / 30.0
    return {
        "time_s": float(item.get("time_s") or 0.0),
        "gas_kpa": float(item.get("gas_kpa") or 0.0),
        "damper_level": max(0.0, min(10.0, float(damper))),
        "rpm_level": max(0.0, min(10.0, float(rpm))),
        "control_scale": "0-10",
        "note": item.get("note") or "",
    }


def add_action(gas, damper, rpm, time_s=0.0, note: str = "") -> Dict:
    return normalize_action({"time_s": time_s, "gas_kpa": gas, "damper_level": damper, "rpm_level": rpm, "note": note})
