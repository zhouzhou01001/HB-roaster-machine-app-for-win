from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

from .events import EVENT_TYPES, validate_event


_EXTRA_NUMERIC_FIELDS = (
    "elapsed_time", "gas_mbar", "gas_kpa", "damper_level", "rpm_level",
    "device_time_s", "capture_time_s",
)
_IMPORTED_SAMPLE_FIELDS = ("work", "work_ror", "gap_before", "control_scale", *_EXTRA_NUMERIC_FIELDS)


def restore_imported_sample(sample: Mapping) -> dict:
    """Restore CSV extension fields retained in the database's raw_payload.

    Returns a new dictionary, retaining explicit top-level values. This can
    be used on load_samples() rows before displaying or re-exporting them;
    no database schema change is required.
    """
    result = dict(sample)
    payload = result.get("raw_payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    saved = payload.get("raw_import_sample", {}) if isinstance(payload, Mapping) else {}
    if not saved:
        saved = result.get("raw_import_sample") or {}
    if isinstance(saved, Mapping):
        for key in _IMPORTED_SAMPLE_FIELDS:
            if result.get(key) is None and key in saved:
                result[key] = saved[key]
    if result.get("work") is None:
        for key in ("CH4", "raw_ch4"):
            if result.get(key) is not None:
                result["work"] = result[key]
                break
    return result


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _seconds(value: str, fallback: float) -> float:
    text = str(value or "").strip()
    if ":" in text:
        parts = text.split(":")
        try:
            return float(parts[-1]) + 60 * float(parts[-2]) + (3600 * float(parts[-3]) if len(parts) == 3 else 0)
        except ValueError:
            return fallback
    try:
        return float(text)
    except ValueError:
        return fallback


def _number(row: Dict[str, str], aliases: Iterable[str]) -> Optional[float]:
    keys = {_normalise(key): value for key, value in row.items()}
    for alias in aliases:
        value = keys.get(_normalise(alias))
        if value not in (None, ""):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def import_csv_batch(db, source: str | Path) -> int:
    path = Path(source)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        preview = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(preview, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(handle, dialect=dialect))
    previous_batch_id = db.current_roast_id
    batch_id = db.create_batch(coffee_name=path.stem)
    savepoint = f"csv_import_{id(rows)}"
    db.conn.execute(f"SAVEPOINT {savepoint}")
    try:
        imported_events = []
        for index, row in enumerate(rows):
            time_ms = _number(row, ("time_ms",))
            fallback_time = time_ms / 1000 if time_ms is not None else float(index)
            time_s = _seconds(
                row.get("time_s") or row.get("elapsed_time") or row.get("Time")
                or row.get("time") or row.get("Time [s]") or "", fallback_time,
            )
            it = _number(row, ("IT", "Inlet Temp", "Inlet Temperature"))
            et = _number(row, ("ET", "Environment Temp", "Environmental Temp", "Exhaust Temp"))
            bt = _number(row, ("BT", "Bean Temp", "Bean Temperature"))
            work = _number(row, ("work", "CH4", "XT", "XT_C", "raw_ch4"))
            if bt is None and et is None and it is None and work is None:
                continue
            ror = _number(row, ("DeltaBT", "RoR", "BT RoR"))
            extra = {key: _number(row, (key,)) for key in _EXTRA_NUMERIC_FIELDS}
            extra.update({
                "work": work,
                "work_ror": _number(row, ("work_ror", "WORK_RoR", "XT RoR", "DeltaXT", "DeltaWork", "CH4 RoR")),
                "control_scale": row.get("control_scale") or None,
            })
            gap = row.get("gap_before")
            if gap not in (None, ""):
                extra["gap_before"] = str(gap).strip().lower() in ("true", "1", "yes", "on")
            source = str(row.get("data_source") or row.get("source") or "IMPORT").strip().upper() or "IMPORT"
            db.insert_sample({
                **extra,
                "time_s": time_s,
                "time_ms": int(time_s * 1000),
                "timestamp": row.get("timestamp") or None,
                "it": it,
                "et": et,
                "bt": bt,
                "it_ror": _number(row, ("DeltaIT", "IT RoR")),
                "et_ror": _number(row, ("DeltaET", "ET RoR")),
                "bt_ror": ror,
                "CH4": work,
                # insert_sample persists raw_* values as JSON, including fields
                # that do not have dedicated columns in the current schema.
                "raw_import_sample": extra,
                "source": source,
                "data_source": source,
                "source_port": row.get("source_port") or row.get("port") or "",
                "raw_frame": row.get("raw_frame") or "",
                "parser_format": row.get("parser_format") or "csv_import",
                "temperature_unit": row.get("temperature_unit") or "C",
                "channel_mapping": row.get("channel_mapping") or "",
                "baudrate": row.get("source_baudrate") or row.get("baudrate") or None,
                "profile": row.get("protocol_profile") or row.get("profile") or "",
                "time_basis": row.get("time_basis") or "capture_relative",
                "parse_error_count": row.get("parse_error_count") or 0,
                "raw_import_source": str(path),
            }, roast_id=batch_id, commit=False)
            event = str(row.get("Event") or row.get("event") or "").upper().replace(" ", "_")
            if event in EVENT_TYPES:
                try:
                    existing = validate_event(imported_events, event, time_s)
                    if existing is not None:
                        raise ValueError(f"duplicate milestone {event}")
                    db.insert_event(batch_id, time_s, event, bt, ror, "Imported", it, et, commit=False)
                except ValueError as exc:
                    raise ValueError(f"CSV row {index + 2}, event {event}: {exc}") from exc
                imported_events.append({"event_type": event, "time_s": time_s})
        db.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return batch_id
    except Exception:
        db.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        db._current_roast_id = previous_batch_id
        raise
