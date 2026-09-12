from __future__ import annotations

import json
from pathlib import Path

from app.roast.actions import normalize_action
from typing import Dict, Iterable, List


def export_roast_json(
    batch_info: Dict,
    samples: List[Dict],
    events: List[Dict],
    actions: List[Dict],
    target: str | Path,
) -> None:
    payload = {
        "batch": batch_info,
        "samples": samples,
        "events": events,
        "manual_actions": [normalize_action(item) for item in actions],
        "count": {
            "samples": len(samples),
            "events": len(events),
            "manual_actions": len(actions),
        },
    }
    Path(target).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
