from __future__ import annotations

import json
from pathlib import Path

from app.roast.actions import normalize_action
from typing import Dict, List, Optional


def build_profile_payload(
    roast_id: Optional[int],
    batch_info: Dict,
    samples: List[Dict],
    events: List[Dict],
    manual_actions: List[Dict],
) -> str:
    payload = {
        "roast_id": roast_id,
        "batch_info": batch_info,
        "samples": samples,
        "events": events,
        "manual_actions": [normalize_action(item) for item in manual_actions],
    }
    return json.dumps(payload, ensure_ascii=False)


def save_profile_from_batch(db, batch_id: int, name: str, notes: str = "", *, source: str = "legacy") -> int:
    session = db.load_history_session(batch_id, source=source)
    if session is None:
        raise ValueError(f"{source} batch {batch_id} does not exist")
    # profiles.roast_id references only roast_info, never the archive ID namespace.
    roast_id = batch_id if source == "legacy" else None
    actions = session["actions"]
    if source == "archive":
        actions = [dict(item) for item in actions]
        for action in actions:
            if action.get("gas_kpa") is None and action.get("gas_mbar") is not None:
                action["gas_kpa"] = float(action["gas_mbar"]) / 10.0
    payload = build_profile_payload(
        roast_id=roast_id,
        batch_info=session["metadata"],
        samples=session["samples"],
        events=session["events"],
        manual_actions=actions,
    )
    if source == "archive":
        archive_payload = json.loads(payload)
        archive_payload.update(source="archive", batch_id=batch_id)
        payload = json.dumps(archive_payload, ensure_ascii=False)
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO profiles (roast_id, name, notes, payload)
        VALUES (?, ?, ?, ?)
        """,
        (roast_id, name, notes, payload),
    )
    db.conn.commit()
    return int(cursor.lastrowid)


def load_reference_profile(db, profile_id: int) -> Dict:
    row = db.conn.execute(
        "SELECT * FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Profile {profile_id} not found")
    profile = dict(row)
    payload = profile.get("payload")
    if isinstance(payload, str):
        try:
            profile["payload"] = json.loads(payload)
        except Exception:
            profile["payload"] = {"raw": payload}
    return profile


def list_reference_profiles(db) -> List[Dict]:
    rows = db.conn.execute(
        "SELECT id, roast_id, name, notes, created_at FROM profiles ORDER BY created_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def export_profile_to_json(profile: Dict, target: str | Path) -> None:
    target = Path(target)
    Path(target).write_text(
        json.dumps(profile.get("payload", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
