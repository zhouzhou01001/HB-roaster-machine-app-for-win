from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional


@dataclass
class ReplayFrame:
    time_s: float
    it: Optional[float]
    et: Optional[float]
    bt: Optional[float]
    it_ror: Optional[float] = None
    et_ror: Optional[float] = None
    bt_ror: Optional[float] = None


class RoastReplay:
    def __init__(self, samples: Iterable[Mapping]):
        self.samples: List[ReplayFrame] = [
            ReplayFrame(
                float(s.get("time_s", 0.0)),
                s.get("it"),
                s.get("et"),
                s.get("bt"),
                s.get("it_ror"),
                s.get("et_ror"),
                s.get("bt_ror"),
            )
            for s in samples
        ]
        self.samples.sort(key=lambda s: s.time_s)

        self.position = 0
        self.speed = 1.0
        self._playing = False

    def reset(self) -> None:
        self.position = 0
        self._playing = False

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.2, float(speed))

    def next(self, callback: Optional[Callable[[ReplayFrame], None]] = None) -> Optional[ReplayFrame]:
        if self.position >= len(self.samples):
            self._playing = False
            return None
        frame = self.samples[self.position]
        self.position += 1
        if callback:
            callback(frame)
        return frame

    def play(self, callback: Callable[[ReplayFrame], None]) -> None:
        self._playing = True
        while self._playing and self.position < len(self.samples):
            frame = self.next(callback)
            if frame is None:
                break
            if self.position < len(self.samples):
                dt = self.samples[self.position].time_s - frame.time_s
                sleep_s = dt / self.speed
                if sleep_s > 0:
                    time.sleep(sleep_s)

    def stop(self) -> None:
        self._playing = False

