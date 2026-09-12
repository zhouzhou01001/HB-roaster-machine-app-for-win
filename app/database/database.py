from __future__ import annotations

import sqlite3
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.roast.events import validate_event

from .migration import migrate


@dataclass(frozen=True)
class SessionArchiveResult:
    """Identifiers produced by one successful atomic session archive."""

    charge_id: int
    batch_id: int
    charge_number: int


class RoastDatabase:
    """
    Lightweight SQLite data store for roast batches.
    Keeps raw samples and calculated annotations in one file.
    """

    def __init__(self, db_path: str | Path = "data/default.hbroast") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)
        self._current_roast_id: Optional[int] = None

    @property
    def current_roast_id(self) -> Optional[int]:
        return self._current_roast_id

    def create_batch(
        self,
        device: str = "",
        operator: str = "",
        coffee_name: str = "",
        green_weight: Optional[float] = None,
        roasted_weight: Optional[float] = None,
        commit: bool = True,
    ) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO roast_info (device, operator, coffee_name, green_weight, roasted_weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device, operator, coffee_name, green_weight, roasted_weight),
        )
        batch_id = int(cursor.lastrowid)
        if commit:
            self.conn.commit()
        self._current_roast_id = batch_id
        return batch_id

    def open_roast(self, roast_id: int) -> None:
        self._current_roast_id = roast_id

    def latest_roast_id(self) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM roast_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None

    def ensure_batch(self) -> int:
        if self._current_roast_id is None:
            latest = self.latest_roast_id()
            if latest is None:
                self.create_batch()
            else:
                self._current_roast_id = latest
        return self._current_roast_id

    def insert_sample(
        self,
        sample: Dict[str, Any],
        roast_id: Optional[int] = None,
        commit: bool = True,
    ) -> int:
        roast_id = roast_id or self.ensure_batch()
        time_ms = int((sample.get("time_ms") if sample.get("time_ms") is not None else sample.get("time_s", 0.0) * 1000))
        row = (
            roast_id,
            time_ms,
            float(sample["time_s"]),
            sample.get("timestamp"),
            sample.get("it"),
            sample.get("et"),
            sample.get("bt"),
            sample.get("it_ror"),
            sample.get("et_ror"),
            sample.get("bt_ror"),
            sample.get("CH1"),
            sample.get("CH2"),
            sample.get("CH3"),
            sample.get("CH4"),
            json.dumps({key: value for key, value in sample.items() if key.startswith("raw_")}, ensure_ascii=False),
            sample.get("source") or sample.get("data_source") or "UNKNOWN",
            sample.get("data_source") or sample.get("source") or "UNKNOWN",
            sample.get("source_port") or sample.get("port") or "",
            sample.get("raw_frame") or "",
            sample.get("parser_format") or "",
            sample.get("temperature_unit") or "C",
            sample.get("channel_mapping") or "",
            sample.get("baudrate") or sample.get("source_baudrate") or None,
            sample.get("profile") or sample.get("protocol_profile") or "",
            sample.get("time_basis") or "capture_relative",
            int(sample.get("parse_error_count") or 0),
        )
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO samples
            (roast_id, time_ms, time_s, timestamp, it, et, bt, it_ror, et_ror, bt_ror,
             raw_ch1, raw_ch2, raw_ch3, raw_ch4, raw_payload, source, data_source,
             source_port, raw_frame, parser_format, temperature_unit, channel_mapping,
             source_baudrate, protocol_profile, time_basis, parse_error_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        if commit:
            self.conn.commit()
        return int(cursor.lastrowid)

    def load_samples(self, roast_id: int) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM samples WHERE roast_id = ? ORDER BY time_s ASC",
            (roast_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_event(
        self,
        roast_id: Optional[int],
        time_s: float,
        event_type: str,
        bt: Optional[float] = None,
        ror: Optional[float] = None,
        note: str = "",
        it: Optional[float] = None,
        et: Optional[float] = None,
        commit: bool = True,
    ) -> int:
        roast_id = roast_id or self.ensure_batch()
        event_type = str(event_type).strip().upper()
        stored = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM events WHERE roast_id = ? ORDER BY time_s ASC, id ASC",
                (roast_id,),
            ).fetchall()
        ]
        existing = validate_event(stored, event_type, time_s)
        if existing is not None:
            return int(existing["id"])
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO events (roast_id, time_s, event_type, bt, ror, note, it, et)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (roast_id, time_s, event_type, bt, ror, note, it, et),
        )
        if commit:
            self.conn.commit()
        return int(cursor.lastrowid)

    def replace_tp_event(self, roast_id: Optional[int], time_s: float, it: Optional[float], et: Optional[float], bt: Optional[float], ror: Optional[float], note: str = "") -> int:
        roast_id = roast_id or self.ensure_batch()
        stored = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM events WHERE roast_id = ? AND event_type != 'TP' ORDER BY time_s ASC, id ASC",
                (roast_id,),
            ).fetchall()
        ]
        validate_event(stored, "TP", time_s)
        self.conn.execute("DELETE FROM events WHERE roast_id = ? AND event_type = 'TP'", (roast_id,))
        cursor = self.conn.execute(
            """
            INSERT INTO events (roast_id, time_s, event_type, bt, ror, note, it, et)
            VALUES (?, ?, 'TP', ?, ?, ?, ?, ?)
            """,
            (roast_id, float(time_s), bt, ror, note, it, et),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def load_events(self, roast_id: int) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE roast_id = ? ORDER BY time_s ASC, id ASC",
            (roast_id,),
        ).fetchall()
        result = []
        seen = set()
        for row in rows:
            event = dict(row)
            event_type = str(event.get("event_type", "")).strip().upper()
            if event_type in seen:
                continue
            seen.add(event_type)
            result.append(event)
        return result

    def insert_manual_action(
        self,
        roast_id: Optional[int],
        time_s: float,
        gas_kpa: float,
        damper_level: float,
        rpm_level: float,
        note: str = "",
        control_scale: str = "0-10",
    ) -> int:
        roast_id = roast_id or self.ensure_batch()
        damper_level = float(damper_level)
        rpm_level = float(rpm_level)
        if damper_level > 10.0:
            damper_level /= 10.0
        if rpm_level > 10.0:
            rpm_level /= 30.0
        if not (0.0 <= damper_level <= 10.0 and 0.0 <= rpm_level <= 10.0):
            raise ValueError("风门和转速档位必须为 0 至 10")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO manual_actions
            (roast_id, time_s, gas_kpa, damper_level, rpm_level, control_scale, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (roast_id, time_s, gas_kpa, damper_level, rpm_level, "0-10", note),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def load_manual_actions(self, roast_id: int) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM manual_actions WHERE roast_id = ? ORDER BY time_s ASC",
            (roast_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            damper = item.get("damper_level")
            rpm = item.get("rpm_level")
            if damper is None:
                damper = float(item.get("damper_pct") or 0.0) / 10.0
            if rpm is None:
                rpm = float(item.get("rpm") or 0.0) / 30.0
            result.append({
                "id": item.get("id"),
                "roast_id": item.get("roast_id"),
                "time_s": float(item.get("time_s") or 0.0),
                "gas_kpa": float(item.get("gas_kpa") or 0.0),
                "damper_level": max(0.0, min(10.0, float(damper))),
                "rpm_level": max(0.0, min(10.0, float(rpm))),
                "control_scale": "0-10",
                "note": item.get("note") or "",
            })
        return result

    def list_roast_ids(self) -> List[int]:
        rows = self.conn.execute(
            "SELECT id FROM roast_info ORDER BY id DESC"
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def list_roasts(self) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM roast_info ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_profiles(self) -> List[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT id, roast_id, name, notes, created_at FROM profiles ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def create_profile(self, roast_id: int, name: str, notes: str, payload: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO profiles (roast_id, name, notes, payload) VALUES (?, ?, ?, ?)",
            (roast_id, name, notes, payload),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def load_profile_payload(self, profile_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT payload FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return row["payload"] if row else None

    def save_cupping(
        self,
        roast_id: int,
        flavor: float | None = None,
        score: float | None = None,
        aroma: float | None = None,
        acidity: float | None = None,
        sweetness: float | None = None,
        body: float | None = None,
        notes: str = "",
    ) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO cupping
            (roast_id, flavor, score, aroma, acidity, sweetness, body, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (roast_id, flavor, score, aroma, acidity, sweetness, body, notes),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def load_cuppings(self, roast_id: int):
        rows = self.conn.execute(
            "SELECT * FROM cupping WHERE roast_id = ? ORDER BY id DESC",
            (roast_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_photo(self, roast_id: int, file_path: str, caption: str = "") -> int:
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO batch_photos (roast_id, file_path, caption) VALUES (?, ?, ?)", (roast_id, file_path, caption))
        self.conn.commit()
        return int(cursor.lastrowid)

    def load_photos(self, roast_id: int) -> List[dict]:
        rows = self.conn.execute("SELECT * FROM batch_photos WHERE roast_id = ? ORDER BY id", (roast_id,)).fetchall()
        return [dict(row) for row in rows]

    def update_roast_info(
        self,
        roast_id: int,
        device: str = "",
        operator: str = "",
        coffee_name: str = "",
        green_weight: float | None = None,
        roasted_weight: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE roast_info
            SET device = ?, operator = ?, coffee_name = ?, green_weight = ?, roasted_weight = ?
            WHERE id = ?
            """,
            (device, operator, coffee_name, green_weight, roasted_weight, roast_id),
        )
        self.conn.commit()

    @staticmethod
    def _archive_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        try:
            result = []
            for row in rows:
                if not isinstance(row, (Mapping, sqlite3.Row)):
                    raise ValueError("Archive rows must be mappings")
                result.append(dict(row))
            return result
        except TypeError as exc:
            raise ValueError("Archive rows must be an iterable of mappings") from exc

    @staticmethod
    def _archive_number(value: Any, name: str, *, minimum: Optional[float] = None) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if isinstance(value, bool) or not math.isfinite(numeric):
            raise ValueError(f"{name} must be a finite number")
        if minimum is not None and numeric < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
        return numeric

    @classmethod
    def _archive_integer(cls, value: Any, name: str, minimum: int = 0) -> int:
        numeric = cls._archive_number(value, name, minimum=minimum)
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if numeric != integer or integer > 9223372036854775807:
            raise ValueError(f"{name} must be an integer within SQLite range")
        return integer

    @classmethod
    def _validate_archive_rows(cls, samples: List[dict], events: List[dict], actions: List[dict]) -> None:
        if not samples:
            raise ValueError("Cannot archive a session without samples")
        for kind, rows in (("sample", samples), ("event", events), ("action", actions)):
            previous = None
            for row in rows:
                time_s = cls._archive_number(row.get("time_s"), f"{kind}.time_s", minimum=0)
                if kind == "sample" and previous is not None and time_s <= previous:
                    raise ValueError("Sample times must increase strictly")
                previous = time_s
                for key in ("it", "et", "bt", "work", "ambient", "it_ror", "et_ror", "bt_ror", "work_ror", "ror", "timestamp"):
                    if row.get(key) is not None:
                        cls._archive_number(row[key], f"{kind}.{key}")
                if kind == "sample" and not any(row.get(key) is not None for key in ("it", "et", "bt", "work")):
                    raise ValueError("Sample must contain a temperature channel")
                if kind == "action":
                    for key in ("gas_mbar", "gas_kpa", "damper_level", "rpm_level"):
                        if row.get(key) is not None:
                            number = cls._archive_number(row[key], key, minimum=0)
                            if key in ("damper_level", "rpm_level") and number > 10:
                                raise ValueError(f"{key} must be between 0 and 10")
        validated = []
        for event in events:
            if validate_event(validated, event.get("event_type", ""), event["time_s"]) is not None:
                raise ValueError("Archive contains duplicate event types")
            validated.append(event)

    @staticmethod
    def _json_payload(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _json_value(value: Optional[str], default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _event_by_type(events: Iterable[Mapping[str, Any]], event_type: str) -> Optional[Dict[str, Any]]:
        target = event_type.strip().upper()
        for event in events:
            if str(event.get("event_type", "")).strip().upper() == target:
                return dict(event)
        return None

    @staticmethod
    def _curve_snapshot(
        samples: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        keys = (
            "time_s",
            "it",
            "et",
            "bt",
            "work",
            "ambient",
            "it_ror",
            "et_ror",
            "bt_ror",
            "work_ror",
        )
        snapshot = {
            key: [row.get(key) for row in samples]
            for key in keys
            if any(key in row for row in samples)
        }
        snapshot["actions"] = actions
        return snapshot

    def archive_session(
        self,
        samples: Iterable[Mapping[str, Any]],
        events: Iterable[Mapping[str, Any]],
        actions: Iterable[Mapping[str, Any]],
        metadata: Optional[Mapping[str, Any]] = None,
        *,
        charge_number: Optional[int] = None,
    ) -> SessionArchiveResult:
        """Atomically archive one in-memory roast session.

        This API does not read from or mutate the legacy roast_info/samples/events/
        manual_actions tables. All input is materialized before the transaction;
        charges and batches are then inserted within one savepoint so a failure in
        either insert leaves no partial archive.
        """

        sample_rows = self._archive_rows(samples)
        event_rows = self._archive_rows(events)
        action_rows = self._archive_rows(actions)
        self._validate_archive_rows(sample_rows, event_rows, action_rows)
        if metadata is not None and not isinstance(metadata, Mapping):
            raise ValueError("Archive metadata must be a mapping")
        archive_metadata = dict(metadata or {})

        requested_number = charge_number
        if requested_number is None and archive_metadata.get("charge_number") is not None:
            requested_number = archive_metadata["charge_number"]
        if requested_number is not None:
            requested_number = self._archive_integer(requested_number, "charge_number", 1)
        requested_batch_number = archive_metadata.get("batch_number")
        if "batch_number" in archive_metadata:
            requested_batch_number = self._archive_integer(requested_batch_number, "batch_number", 1)

        sample_times = [
            float(row["time_s"])
            for row in sample_rows
            if row.get("time_s") is not None
        ]
        duration_value = archive_metadata.get("duration_s")
        duration_s = (
            self._archive_number(duration_value, "duration_s", minimum=0)
            if duration_value is not None
            else max(sample_times, default=0.0)
        )
        if duration_s < max(sample_times):
            raise ValueError("duration_s cannot precede the last sample")

        drop_event = self._event_by_type(event_rows, "DROP")
        fc_start_event = self._event_by_type(event_rows, "FC_START")
        drop_time_s = self._optional_float(
            archive_metadata.get(
                "drop_time_s",
                None if drop_event is None else drop_event.get("time_s"),
            )
        )
        drop_bt = self._optional_float(
            archive_metadata.get(
                "drop_bt",
                None if drop_event is None else drop_event.get("bt"),
            )
        )
        fc_start_s = self._optional_float(
            archive_metadata.get(
                "fc_start_s",
                None if fc_start_event is None else fc_start_event.get("time_s"),
            )
        )
        for name, value in (("drop_time_s", drop_time_s), ("fc_start_s", fc_start_s)):
            if value is not None:
                self._archive_number(value, name, minimum=0)
        if drop_bt is not None:
            self._archive_number(drop_bt, "drop_bt")
        charge_weight_g = archive_metadata.get("charge_weight_g", archive_metadata.get("green_weight"))
        if charge_weight_g not in (None, ""):
            charge_weight_g = self._archive_number(charge_weight_g, "charge_weight_g", minimum=0)
        else:
            charge_weight_g = None
        stage_log = list(archive_metadata.get("stage_log") or [])
        curve_snapshot = archive_metadata.get("curve_snapshot")
        if curve_snapshot is None:
            curve_snapshot = self._curve_snapshot(sample_rows, action_rows)

        default_action_count = max(0, len(action_rows) - 1)
        gas_action_count = self._archive_integer(archive_metadata.get("gas_action_count", default_action_count), "gas_action_count")
        damper_action_count = self._archive_integer(archive_metadata.get("damper_action_count", default_action_count), "damper_action_count")
        rpm_action_count = self._archive_integer(archive_metadata.get("rpm_action_count", default_action_count), "rpm_action_count")
        if min(gas_action_count, damper_action_count, rpm_action_count) < 0:
            raise ValueError("action counts cannot be negative")

        def archive_json(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)

        samples_json = archive_json(sample_rows)
        events_json = archive_json(event_rows)
        actions_json = archive_json(action_rows)
        metadata_json = archive_json(archive_metadata)
        stage_log_json = archive_json(stage_log)
        curve_snapshot_json = archive_json(curve_snapshot)

        savepoint = "archive_session"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            if requested_number is None:
                row = self.conn.execute(
                    "SELECT COALESCE(MAX(charge_number), 0) + 1 AS next_number FROM charges"
                ).fetchone()
                requested_number = int(row["next_number"])
            batch_number = requested_batch_number if requested_batch_number is not None else requested_number
            if batch_number < 1:
                raise ValueError("batch_number must be at least 1")

            charge_cursor = self.conn.execute(
                """
                INSERT INTO charges
                (charge_number, started_at, ended_at, duration_s, drop_time_s,
                 sample_count, event_count, action_count, samples_json, events_json,
                 actions_json, metadata_json, stage_log_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    requested_number,
                    archive_metadata.get("started_at"),
                    archive_metadata.get("ended_at"),
                    duration_s,
                    drop_time_s,
                    len(sample_rows),
                    len(event_rows),
                    len(action_rows),
                    samples_json,
                    events_json,
                    actions_json,
                    metadata_json,
                    stage_log_json,
                ),
            )
            charge_id = int(charge_cursor.lastrowid)

            batch_cursor = self.conn.execute(
                """
                INSERT INTO batches
                (charge_id, batch_number, coffee_name, bean_origin, charge_weight_g,
                 duration_s, drop_bt, fc_start_s, gas_action_count,
                 damper_action_count, rpm_action_count, curve_snapshot_json,
                 metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    charge_id,
                    batch_number,
                    archive_metadata.get("coffee_name"),
                    archive_metadata.get("bean_origin") or archive_metadata.get("origin"),
                    charge_weight_g,
                    duration_s,
                    drop_bt,
                    fc_start_s,
                    gas_action_count,
                    damper_action_count,
                    rpm_action_count,
                    curve_snapshot_json,
                    metadata_json,
                ),
            )
            batch_id = int(batch_cursor.lastrowid)
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

        return SessionArchiveResult(
            charge_id=charge_id,
            batch_id=batch_id,
            charge_number=requested_number,
        )

    def list_charges(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, charge_number, started_at, ended_at, duration_s,
                   drop_time_s, sample_count, event_count, action_count, archived_at
            FROM charges
            ORDER BY charge_number DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def load_charge(self, charge_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM charges WHERE id = ?",
            (int(charge_id),),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["samples"] = self._json_value(result.pop("samples_json"), [])
        result["events"] = self._json_value(result.pop("events_json"), [])
        result["actions"] = self._json_value(result.pop("actions_json"), [])
        result["metadata"] = self._json_value(result.pop("metadata_json"), {})
        result["stage_log"] = self._json_value(result.pop("stage_log_json"), [])
        return result

    def list_batches(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, charge_id, batch_number, coffee_name, bean_origin,
                   charge_weight_g, duration_s, drop_bt, fc_start_s,
                   gas_action_count, damper_action_count, rpm_action_count, created_at
            FROM batches
            ORDER BY batch_number DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def load_batch(self, batch_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM batches WHERE id = ?",
            (int(batch_id),),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["curve_snapshot"] = self._json_value(result.pop("curve_snapshot_json"), {})
        result["metadata"] = self._json_value(result.pop("metadata_json"), {})
        return result

    def list_history_sessions(self) -> List[Dict[str, Any]]:
        """List both ID namespaces. Pass each row's source and id to the reader."""
        return [
            {**row, "source": source}
            for source, rows in (("archive", self.list_batches()), ("legacy", self.list_roasts()))
            for row in rows
        ]

    def load_history_session(self, record_id: int, *, source: str) -> Optional[Dict[str, Any]]:
        """Read an explicit archive batch or legacy roast without changing active state.

        Returns source, id, metadata, samples, events and actions, or None if absent.
        IDs overlap between stores, so source is mandatory and never inferred.
        """
        if source == "archive":
            batch = self.load_batch(record_id)
            if batch is None:
                return None
            charge = self.load_charge(batch["charge_id"])
            if charge is None:
                raise ValueError(f"Archive batch {record_id} has no charge snapshot")
            metadata = {**batch, **charge["metadata"], **batch["metadata"]}
            metadata.setdefault("green_weight", metadata.get("charge_weight_g"))
            metadata.setdefault("origin", metadata.get("bean_origin"))
            return {
                "source": source, "id": int(record_id),
                "metadata": metadata,
                "samples": charge["samples"], "events": charge["events"],
                "actions": charge["actions"],
            }
        if source == "legacy":
            roast = self.conn.execute("SELECT * FROM roast_info WHERE id = ?", (record_id,)).fetchone()
            if roast is None:
                return None
            return {
                "source": source, "id": int(record_id), "metadata": dict(roast),
                "samples": self.load_samples(record_id), "events": self.load_events(record_id),
                "actions": self.load_manual_actions(record_id),
            }
        raise ValueError("source must be 'archive' or 'legacy'")

    def update_history_metadata(
        self, record_id: int, payload: Mapping[str, Any], *, source: str = "legacy"
    ) -> None:
        """Edit existing batch information in an explicit ID namespace.

        Legacy uses update_roast_info's full replacement/defaults contract.
        Archive patches device/operator/name/origin/weights, preserving other
        metadata and all recorded data. Its two updates share one savepoint.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("History metadata must be a mapping")
        if source == "legacy":
            self.update_roast_info(
                record_id,
                device=payload.get("device", ""),
                operator=payload.get("operator", ""),
                coffee_name=payload.get("coffee_name", ""),
                green_weight=payload.get("green_weight"),
                roasted_weight=payload.get("roasted_weight"),
            )
            return
        if source != "archive":
            raise ValueError("source must be 'archive' or 'legacy'")
        changes = dict(payload)
        editable = {"device", "operator", "coffee_name", "origin", "bean_origin",
                    "green_weight", "charge_weight_g", "roasted_weight"}
        if changes.keys() - editable:
            raise ValueError("Only batch information fields can be edited")
        for key in ("device", "operator", "coffee_name", "origin", "bean_origin"):
            if key in changes and changes[key] is not None and not isinstance(changes[key], str):
                raise ValueError(f"{key} must be text or None")
        for key in ("green_weight", "charge_weight_g", "roasted_weight"):
            if key in changes:
                changes[key] = (None if changes[key] in (None, "") else
                                self._archive_number(changes[key], key, minimum=0))
        if "green_weight" in changes and "charge_weight_g" in changes:
            if changes["green_weight"] != changes["charge_weight_g"]:
                raise ValueError("green_weight and charge_weight_g must agree")
        if "green_weight" in changes or "charge_weight_g" in changes:
            weight = changes.get("charge_weight_g", changes.get("green_weight"))
            changes.update(green_weight=weight, charge_weight_g=weight)
        if "origin" in changes or "bean_origin" in changes:
            origin = changes.get("bean_origin", changes.get("origin"))
            changes.update(origin=origin, bean_origin=origin)

        savepoint = "update_history_metadata"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            batch = self.load_batch(record_id)
            if batch is None:
                raise ValueError(f"Archive batch {record_id} does not exist")
            charge = self.load_charge(batch["charge_id"])
            if charge is None:
                raise ValueError(f"Archive batch {record_id} has no charge snapshot")
            charge_metadata = {**charge["metadata"], **changes}
            batch_metadata = {**batch["metadata"], **changes}
            charge_json = json.dumps(charge_metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
            batch_json = json.dumps(batch_metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
            self.conn.execute("UPDATE charges SET metadata_json = ? WHERE id = ?",
                              (charge_json, batch["charge_id"]))
            self.conn.execute(
                """UPDATE batches SET metadata_json = ?, coffee_name = ?, bean_origin = ?,
                   charge_weight_g = ? WHERE id = ?""",
                (batch_json, changes.get("coffee_name", batch["coffee_name"]),
                 changes.get("bean_origin", batch["bean_origin"]),
                 changes.get("charge_weight_g", batch["charge_weight_g"]), record_id),
            )
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def get_config_value(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value_json FROM config WHERE key = ?",
            (str(key),),
        ).fetchone()
        if row is None:
            return default
        return self._json_value(row["value_json"], default)

    def list_config_values(self) -> Dict[str, Any]:
        rows = self.conn.execute(
            "SELECT key, value_json FROM config ORDER BY key"
        ).fetchall()
        return {
            str(row["key"]): self._json_value(row["value_json"], None)
            for row in rows
        }

    def set_config_value(self, key: str, value: Any) -> None:
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("config key cannot be empty")
        value_json = self._json_payload(value)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO config (key, value_json, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = datetime('now')
                """,
                (normalized_key, value_json),
            )

    def close(self) -> None:
        if self.conn:
            try:
                self.conn.commit()
            except Exception:
                pass
            self.conn.close()
            self.conn = None  # type: ignore[assignment]

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
