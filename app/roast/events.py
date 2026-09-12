from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional

EVENT_TYPES = (
    "TP",
    "CHARGE",
    "YELLOW",
    "FC_START",
    "FC_END",
    "SC_START",
    "SC_END",
    "DROP",
)

MILESTONE_ORDER = (
    "CHARGE",
    "TP",
    "YELLOW",
    "FC_START",
    "FC_END",
    "SC_START",
    "SC_END",
    "DROP",
)


@dataclass(frozen=True)
class RoastEvent:
    event_type: str
    time_s: float
    bt: Optional[float] = None
    ror: Optional[float] = None
    note: str = ""


def validate_event(storage: List[Dict], event_type: str, time_s: float) -> Optional[Dict]:
    event_type = str(event_type).strip().upper()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported event type: {event_type}")
    try:
        event_time = float(time_s)
    except (TypeError, ValueError) as exc:
        raise ValueError("Event time must be a number") from exc
    if not math.isfinite(event_time) or event_time < 0:
        raise ValueError("Event time must be a finite non-negative number")

    existing = next(
        (item for item in storage if str(item.get("event_type", "")).strip().upper() == event_type),
        None,
    )
    if existing is not None:
        return existing

    rank = MILESTONE_ORDER.index(event_type)
    for item in storage:
        other_type = str(item.get("event_type", "")).strip().upper()
        if other_type not in MILESTONE_ORDER:
            continue
        try:
            other_time = float(item.get("time_s"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(other_time):
            continue
        other_rank = MILESTONE_ORDER.index(other_type)
        if (other_rank < rank and other_time > event_time) or (
            other_rank > rank and other_time < event_time
        ):
            raise ValueError(
                f"Event {event_type} at {event_time:.1f}s conflicts with "
                f"{other_type} at {other_time:.1f}s"
            )
    return None


def add_event(storage: List[Dict], event_type: str, time_s: float, bt: Optional[float], ror: Optional[float], note: str = "") -> Dict:
    event_type = str(event_type).strip().upper()
    existing = validate_event(storage, event_type, time_s)
    if existing is not None:
        return existing
    event = {
        "event_type": event_type,
        "time_s": float(time_s),
        "bt": bt,
        "ror": ror,
        "note": note or "",
    }
    storage.append(event)
    return event
