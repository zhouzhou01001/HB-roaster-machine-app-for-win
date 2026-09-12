from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


def _align_series(
    base_samples: Iterable[Dict],
    target_samples: Iterable[Dict],
    channels=("it", "et", "bt"),
    dt=1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    base = np.array([s["time_s"] for s in base_samples], dtype=float)
    target = np.array([s["time_s"] for s in target_samples], dtype=float)

    if len(base) == 0 or len(target) == 0:
        return np.array([]), np.array([]), np.array([])

    end_t = min(base[-1], target[-1])
    start_t = max(base[0], target[0])
    times = np.arange(start_t, end_t + 1e-6, dt)
    if times.size == 0:
        return np.array([]), np.array([]), np.array([])

    def interp(series_samples, key):
        x = np.array([s["time_s"] for s in series_samples], dtype=float)
        y = np.array([s.get(key) for s in series_samples], dtype=float)
        valid = ~np.isnan(y)
        if valid.sum() < 2:
            return np.full_like(times, np.nan, dtype=float)
        x2 = x[valid]
        y2 = y[valid]
        return np.interp(times, x2, y2)

    base_bt = interp(list(base_samples), "bt")
    target_bt = interp(list(target_samples), "bt")
    return times, base_bt, target_bt


def compute_curve_metrics(base_samples: Iterable[Dict], target_samples: Iterable[Dict]) -> Dict:
    times, base_bt, target_bt = _align_series(base_samples, target_samples)
    if times.size == 0:
        return {
            "mae_bt": None,
            "rmse_bt": None,
            "max_delta_bt": None,
            "duration": 0.0,
        }

    mask = np.isfinite(base_bt) & np.isfinite(target_bt)
    if mask.sum() == 0:
        return {
            "mae_bt": None,
            "rmse_bt": None,
            "max_delta_bt": None,
            "duration": float(times[-1] - times[0]),
        }

    delta = target_bt[mask] - base_bt[mask]
    mae = float(np.mean(np.abs(delta)))
    rmse = float(np.sqrt(np.mean(np.square(delta))))
    max_delta = float(np.max(np.abs(delta)))
    return {
        "mae_bt": mae,
        "rmse_bt": rmse,
        "max_delta_bt": max_delta,
        "duration": float(times[-1] - times[0]),
    }


def event_time(events: Iterable[Dict], event_type: str) -> Optional[float]:
    row = next((item for item in events if item.get("event_type") == event_type), None)
    return float(row["time_s"]) if row and row.get("time_s") is not None else None


def roast_landmarks(events: Iterable[Dict]) -> Dict[str, Optional[float]]:
    items = list(events)
    charge = event_time(items, "CHARGE")
    fc_start = event_time(items, "FC_START")
    drop = event_time(items, "DROP")
    development = None
    development_ratio = None
    if fc_start is not None and drop is not None and drop > fc_start:
        development = drop - fc_start
        if charge is not None and drop > charge:
            development_ratio = development / (drop - charge) * 100.0
    return {"charge": charge, "fc_start": fc_start, "drop": drop, "development_s": development, "development_ratio": development_ratio}


def development_metrics(events: Iterable[Dict], current_time_s: Optional[float] = None) -> Dict[str, Optional[float]]:
    """Calculate the active or completed development phase from FC_START."""
    landmarks = roast_landmarks(events)
    phase_end = landmarks["drop"] if landmarks["drop"] is not None else current_time_s
    fc_start = landmarks["fc_start"]
    charge = landmarks["charge"]
    development_s = None
    development_ratio = None
    if fc_start is not None and phase_end is not None and phase_end >= fc_start:
        development_s = phase_end - fc_start
        if charge is not None and phase_end > charge:
            development_ratio = development_s / (phase_end - charge) * 100.0
    return {
        "start_s": fc_start,
        "end_s": phase_end,
        "development_s": development_s,
        "development_ratio": development_ratio,
        "complete": landmarks["drop"] is not None,
    }


def detect_tp(samples: Iterable[Dict], min_recover=3.0, min_width=8, min_depth=1.0) -> Optional[Dict]:
    seq = list(samples)
    if len(seq) < min_width:
        return None
    bt = [float(s.get("bt") or np.nan) for s in seq]
    t = [float(s.get("time_s") or 0.0) for s in seq]
    for i in range(min_width, len(bt) - min_width):
        before = bt[i - min_width : i + 1]
        after = bt[i + 1 : i + min_width + 1]
        if not np.all(np.isfinite(before + after)):
            continue
        if bt[i] <= np.min(before) and after[-1] - bt[i] >= min_recover and np.all(np.diff(after) >= 0):
            return {
                "time_s": t[i],
                "bt": bt[i],
                "ror": seq[i].get("bt_ror"),
            }
    return None
