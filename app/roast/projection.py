from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np

from .ror import calculate_ror


def predict_ror(
    samples: Iterable[Mapping[str, Optional[float]]],
    future_seconds: int = 60,
    window: float = 10,
    step: float = 1.0,
) -> Dict[str, List[Tuple[float, Optional[float]]]]:
    samples_list: List[Mapping[str, Optional[float]]] = list(samples)
    if not samples_list:
        return {"IT_RoR": [], "ET_RoR": [], "BT_RoR": []}

    last_time = samples_list[-1].get("time_s", 0.0)
    if last_time is None:
        return {"IT_RoR": [], "ET_RoR": [], "BT_RoR": []}

    result: Dict[str, List[Tuple[float, Optional[float]]]] = {
        "IT_RoR": [],
        "ET_RoR": [],
        "BT_RoR": [],
    }
    current = calculate_ror(samples_list, window=window)
    trend = _calc_ror_trend(samples_list, window=window)

    for i in range(0, future_seconds + 1):
        t = float(last_time) + i
        dt = i / 60.0
        for channel in ("IT", "ET", "BT"):
            base = current.get(f"{channel}_RoR")
            if base is None:
                continue
            slope = trend.get(f"{channel}_slope") or 0.0
            pred = float(base) + slope * dt * 60.0
            result[f"{channel}_RoR"].append((t, pred))
    return result


def _calc_ror_trend(
    samples: List[Mapping[str, Optional[float]]],
    window: float = 10,
) -> Dict[str, Optional[float]]:
    # Approximate RoRoR as d(RoR)/dt using sliding trend of recent values.
    if len(samples) < 4:
        return {"IT_slope": 0.0, "ET_slope": 0.0, "BT_slope": 0.0}

    ror_points = []
    last_time = float(samples[-1].get("time_s", 0.0))
    for idx in range(len(samples)):
        segment = samples[: idx + 1]
        if float(segment[-1].get("time_s", 0.0)) < last_time - window:
            continue
        ror = calculate_ror(segment, window=window)
        ror_points.append(
            (
                float(segment[-1].get("time_s", 0.0)),
                ror,
            )
        )

    slopes: Dict[str, float] = {"IT_slope": 0.0, "ET_slope": 0.0, "BT_slope": 0.0}
    for channel in ("IT", "ET", "BT"):
        x = []
        y = []
        for t, vals in ror_points:
            v = vals.get(f"{channel}_RoR")
            if v is None:
                continue
            x.append(t)
            y.append(v)
        if len(x) < 2:
            continue
        x_arr = np.array(x, dtype=float)
        y_arr = np.array(y, dtype=float)
        x_arr = x_arr - x_arr[0]
        try:
            slope, _ = np.polyfit(x_arr, y_arr, deg=1)
            slopes[f"{channel}_slope"] = float(slope)
        except Exception:
            slopes[f"{channel}_slope"] = 0.0

    return slopes

