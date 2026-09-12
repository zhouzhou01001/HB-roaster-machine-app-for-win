"""Pure domain model for roast application configuration.

The model deliberately owns no persistence or device integration.  Callers may
stage a complete candidate configuration, inspect a side-effect-free diff, and
promote that candidate when the next batch starts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class Theme(str, Enum):
    """Supported application themes."""

    DARK = "dark"
    LIGHT = "light"


class HardwareProtocol(str, Enum):
    """Supported acquisition protocols."""

    HB_MODEL_S = "HB_MODEL_S"
    HB_TC4 = "HB_TC4"
    HB_LEGACY_3 = "HB_LEGACY_3"
    SIMULATION = "SIMULATION"


class EffectiveTiming(str, Enum):
    """When a configuration field is expected to take effect operationally."""

    IMMEDIATE = "immediate"
    NEXT_BATCH = "next_batch"
    NEXT_CLEANUP = "next_cleanup"
    ON_RECONNECT = "on_reconnect"


SAMPLE_RATES_HZ = frozenset({1, 2, 4, 5, 10})


def _require_integer(name: str, value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _coerce_enum(name: str, value: object, enum_type: type[Enum]) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}") from exc


@dataclass(frozen=True, slots=True)
class RoastConfiguration:
    """A validated, immutable configuration snapshot."""

    sample_rate_hz: int = 1
    ror_window_seconds: int = 10
    theme: Theme = Theme.DARK
    auto_tp_enabled: bool = False
    data_retention_days: int = 90
    hardware_protocol: HardwareProtocol = HardwareProtocol.HB_MODEL_S

    def __post_init__(self) -> None:
        sample_rate = _require_integer("sample_rate_hz", self.sample_rate_hz, 1, 10)
        if sample_rate not in SAMPLE_RATES_HZ:
            allowed = ", ".join(str(value) for value in sorted(SAMPLE_RATES_HZ))
            raise ValueError(f"sample_rate_hz must be one of: {allowed}")

        _require_integer("ror_window_seconds", self.ror_window_seconds, 1, 60)
        _require_integer("data_retention_days", self.data_retention_days, 30, 365)
        if type(self.auto_tp_enabled) is not bool:
            raise TypeError("auto_tp_enabled must be a bool")

        object.__setattr__(self, "theme", _coerce_enum("theme", self.theme, Theme))
        object.__setattr__(
            self,
            "hardware_protocol",
            _coerce_enum("hardware_protocol", self.hardware_protocol, HardwareProtocol),
        )


@dataclass(frozen=True, slots=True)
class ConfigurationDifference:
    """One field in a current-to-pending configuration preview."""

    field: str
    label: str
    current_value: int | bool | str
    pending_value: int | bool | str
    effective_timing: EffectiveTiming

    def as_dict(self) -> dict[str, int | bool | str]:
        """Return detached, serializable preview data suitable for presentation."""

        return {
            "field": self.field,
            "label": self.label,
            "current": self.current_value,
            "pending": self.pending_value,
            "effective_timing": self.effective_timing.value,
        }


_FIELD_METADATA = (
    ("sample_rate_hz", "采样频率", EffectiveTiming.NEXT_BATCH),
    ("ror_window_seconds", "RoR 平滑窗口", EffectiveTiming.IMMEDIATE),
    ("theme", "主题", EffectiveTiming.IMMEDIATE),
    ("auto_tp_enabled", "自动 TP", EffectiveTiming.IMMEDIATE),
    ("data_retention_days", "数据保留", EffectiveTiming.NEXT_CLEANUP),
    ("hardware_protocol", "硬件协议", EffectiveTiming.ON_RECONNECT),
)
_CONFIGURATION_FIELDS = frozenset(field for field, _, _ in _FIELD_METADATA)


def _serializable_value(value: Any) -> int | bool | str:
    return value.value if isinstance(value, Enum) else value


class RoastConfigurationState:
    """Own current and pending configuration snapshots in memory.

    Staging is transactional: invalid changes leave both snapshots untouched.
    The pending snapshot is promoted atomically by :meth:`start_next_batch`.
    """

    def __init__(self, current: RoastConfiguration | None = None) -> None:
        if current is not None and not isinstance(current, RoastConfiguration):
            raise TypeError("current must be a RoastConfiguration")
        self._current = current or RoastConfiguration()
        self._pending: RoastConfiguration | None = None

    @property
    def current(self) -> RoastConfiguration:
        return self._current

    @property
    def pending(self) -> RoastConfiguration | None:
        return self._pending

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def stage(self, **changes: object) -> RoastConfiguration:
        """Validate and stage changes without altering the current snapshot."""

        unknown = set(changes) - _CONFIGURATION_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown configuration field(s): {names}")

        base = self._pending or self._current
        candidate = replace(base, **changes)
        self._pending = None if candidate == self._current else candidate
        return candidate

    def discard_pending(self) -> None:
        self._pending = None

    def preview(self) -> tuple[ConfigurationDifference, ...]:
        """Return an immutable diff without changing current or pending state."""

        if self._pending is None:
            return ()

        differences: list[ConfigurationDifference] = []
        for field, label, timing in _FIELD_METADATA:
            current_value = getattr(self._current, field)
            pending_value = getattr(self._pending, field)
            if current_value != pending_value:
                differences.append(
                    ConfigurationDifference(
                        field=field,
                        label=label,
                        current_value=_serializable_value(current_value),
                        pending_value=_serializable_value(pending_value),
                        effective_timing=timing,
                    )
                )
        return tuple(differences)

    def preview_data(self) -> tuple[dict[str, int | bool | str], ...]:
        """Return fresh presentation dictionaries; mutating them cannot affect state."""

        return tuple(difference.as_dict() for difference in self.preview())

    def start_next_batch(self) -> RoastConfiguration:
        """Apply the pending snapshot, if present, and return the active snapshot."""

        if self._pending is not None:
            self._current = self._pending
            self._pending = None
        return self._current


__all__ = [
    "ConfigurationDifference",
    "EffectiveTiming",
    "HardwareProtocol",
    "RoastConfiguration",
    "RoastConfigurationState",
    "SAMPLE_RATES_HZ",
    "Theme",
]
