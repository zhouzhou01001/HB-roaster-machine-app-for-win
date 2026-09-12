from __future__ import annotations

from typing import Iterable, Mapping


def predict_milestone_times(samples: Iterable[Mapping], yellow_target_c: float, fc_target_c: float) -> dict[str, float | None]:
    """Estimate absolute milestone times from the latest reliable BT RoR reading."""
    rows = list(samples)
    if not rows:
        return {"yellow_eta_s": None, "fc_eta_s": None, "ror_c_per_min": None}
    latest = rows[-1]
    try:
        bt = float(latest.get("bt"))
        now = float(latest.get("time_s"))
    except (TypeError, ValueError):
        return {"yellow_eta_s": None, "fc_eta_s": None, "ror_c_per_min": None}
    ror = next((row.get("bt_ror") for row in reversed(rows) if row.get("bt_ror") is not None), None)
    try:
        ror = float(ror)
    except (TypeError, ValueError):
        return {"yellow_eta_s": None, "fc_eta_s": None, "ror_c_per_min": None}
    if ror <= 0.15:
        return {"yellow_eta_s": None, "fc_eta_s": None, "ror_c_per_min": ror}
    def eta(target: float) -> float:
        return now if bt >= target else now + (target - bt) / ror * 60.0
    return {"yellow_eta_s": eta(float(yellow_target_c)), "fc_eta_s": eta(float(fc_target_c)), "ror_c_per_min": ror}