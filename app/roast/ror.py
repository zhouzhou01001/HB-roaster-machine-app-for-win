from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


ROR_CHANNELS: Sequence[str] = ("it", "et", "bt", "work")
MIN_UNIQUE_POINTS = 3
MIN_WINDOW_SECONDS = 1.0
MAX_WINDOW_SECONDS = 60.0


def calculate_ror(
    samples: Iterable[Mapping[str, Optional[float]]],
    window: float = 10,
) -> Dict[str, Optional[float]]:
    """Return time-weighted local-linear RoR values in degrees C per minute.

    Lowercase keys are the persisted application contract. Uppercase keys are
    compatibility aliases. ``work`` is the existing application field for the
    fourth temperature channel, displayed as exhaust/outlet temperature.
    """
    window_s = _validate_window(window)
    sample_list: List[Mapping[str, Optional[float]]] = list(samples)
    # A reconnect is a measured discontinuity, never a slope across missing data.
    for index in range(len(sample_list) - 1, -1, -1):
        if sample_list[index].get("gap_before"):
            sample_list = sample_list[index:]
            break
    result = _empty_result()
    anchor_time = _latest_finite_time(sample_list)
    if anchor_time is None:
        return result

    for channel in ROR_CHANNELS:
        value = _calc_channel_ror(sample_list, channel, window_s, anchor_time)
        result[f"{channel}_ror"] = value
        result[f"{channel.upper()}_RoR"] = value
    return result


def _empty_result() -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for channel in ROR_CHANNELS:
        result[f"{channel}_ror"] = None
        result[f"{channel.upper()}_RoR"] = None
    return result


def _validate_window(window: float) -> float:
    try:
        window_s = float(window)
    except (TypeError, ValueError) as exc:
        raise ValueError("RoR window must be a finite number from 1 to 60 seconds") from exc
    if not math.isfinite(window_s) or not MIN_WINDOW_SECONDS <= window_s <= MAX_WINDOW_SECONDS:
        raise ValueError("RoR window must be a finite number from 1 to 60 seconds")
    return window_s


def _latest_finite_time(samples: List[Mapping[str, Optional[float]]]) -> Optional[float]:
    times: List[float] = []
    for sample in samples:
        try:
            value = float(sample.get("time_s"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            times.append(value)
    return max(times) if times else None


def _calc_channel_ror(
    samples: List[Mapping[str, Optional[float]]],
    channel: str,
    window_s: float,
    anchor_time: float,
) -> Optional[float]:
    window_start = anchor_time - window_s
    # Assignment preserves the last valid value in original input order when
    # the device emits more than one reading with the same timestamp.
    values_by_time: Dict[float, float] = {}
    for sample in samples:
        try:
            timestamp = float(sample.get("time_s"))
            value = float(sample.get(channel))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(timestamp) or not math.isfinite(value):
            continue
        if window_start <= timestamp <= anchor_time:
            values_by_time[timestamp] = value

    points = sorted(values_by_time.items())
    if len(points) < MIN_UNIQUE_POINTS:
        return None

    first_time = points[0][0]
    last_time = points[-1][0]
    actual_span = last_time - first_time
    minimum_span = max(1.0, min(5.0, window_s * 0.5))
    if actual_span < minimum_span:
        return None

    x = [timestamp - first_time for timestamp, _ in points]
    y = [value for _, value in points]
    objective_weights = [0.35 + 0.65 * (elapsed / actual_span) for elapsed in x]
    weight_sum = math.fsum(objective_weights)
    x_mean = math.fsum(weight * elapsed for weight, elapsed in zip(objective_weights, x)) / weight_sum
    y_mean = math.fsum(weight * value for weight, value in zip(objective_weights, y)) / weight_sum
    denominator = math.fsum(weight * (elapsed - x_mean) ** 2 for weight, elapsed in zip(objective_weights, x))
    if denominator <= 0.0 or not math.isfinite(denominator):
        return None
    numerator = math.fsum(
        weight * (elapsed - x_mean) * (value - y_mean)
        for weight, elapsed, value in zip(objective_weights, x, y)
    )
    ror = numerator / denominator * 60.0
    return float(ror) if math.isfinite(ror) else None
