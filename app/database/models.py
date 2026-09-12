from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RoastSample:
    time_ms: int
    time_s: float
    it: Optional[float]
    et: Optional[float]
    bt: Optional[float]
    it_ror: Optional[float] = None
    et_ror: Optional[float] = None
    bt_ror: Optional[float] = None


@dataclass(frozen=True)
class RoastEvent:
    time_s: float
    event_type: str
    bt: Optional[float]
    ror: Optional[float]
    note: str = ""


@dataclass(frozen=True)
class ManualAction:
    time_s: float
    gas_kpa: float
    damper_level: float
    rpm_level: float
    note: str = ""

    @property
    def damper_pct(self) -> float:
        return self.damper_level * 10.0

    @property
    def rpm(self) -> float:
        return self.rpm_level * 30.0
