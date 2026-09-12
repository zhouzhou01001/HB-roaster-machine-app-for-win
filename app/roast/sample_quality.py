from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional


@dataclass(frozen=True)
class SampleDecision:
    accepted: bool
    reason: str = ""


class SampleQualityGate:
    """Reject implausible incoming readings without altering accepted raw data."""

    def __init__(self, min_temp: float = -30.0, max_temp: float = 350.0, max_rate_per_min: float = 80.0) -> None:
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.max_rate_per_min = max_rate_per_min
        self._previous: Optional[dict] = None

    def reset(self) -> None:
        self._previous = None

    def accept(self, sample: Mapping) -> SampleDecision:
        try:
            time_s = float(sample["time_s"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return SampleDecision(False, "采样时间无效")
        if not math.isfinite(time_s):
            return SampleDecision(False, "采样时间无效")

        values = {}
        for channel in ("it", "et", "bt", "work"):
            value = sample.get(channel)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return SampleDecision(False, f"{channel.upper()} 温度无效")
            if not self.min_temp <= numeric <= self.max_temp:
                return SampleDecision(False, f"{channel.upper()} 超出温度范围")
            values[channel] = numeric

        if not values:
            return SampleDecision(False, "没有有效温度通道")
        if self._previous is not None:
            elapsed = time_s - self._previous["time_s"]
            if elapsed <= 0:
                return SampleDecision(False, "采样时间未递增")
            if elapsed <= 5.0:
                for channel, value in values.items():
                    previous = self._previous.get(channel)
                    if previous is not None and abs(value - previous) / elapsed * 60.0 > self.max_rate_per_min:
                        return SampleDecision(False, f"{channel.upper()} 疑似尖峰")

        self._previous = {"time_s": time_s, **values}
        return SampleDecision(True)
