from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 0)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roast_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            device TEXT,
            operator TEXT,
            coffee_name TEXT,
            green_weight REAL,
            roasted_weight REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER NOT NULL,
            time_ms INTEGER NOT NULL,
            time_s REAL NOT NULL,
            it REAL,
            et REAL,
            bt REAL,
            it_ror REAL,
            et_ror REAL,
            bt_ror REAL,
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_samples_roast_time
        ON samples (roast_id, time_s)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER NOT NULL,
            time_s REAL NOT NULL,
            event_type TEXT NOT NULL,
            bt REAL,
            ror REAL,
            note TEXT,
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER NOT NULL,
            time_s REAL NOT NULL,
            gas_kpa REAL,
            damper_level REAL,
            rpm_level REAL,
            control_scale TEXT NOT NULL DEFAULT 'legacy',
            damper_pct REAL,
            rpm REAL,
            note TEXT,
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER,
            name TEXT NOT NULL,
            notes TEXT,
            payload TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cupping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER NOT NULL,
            flavor REAL,
            score REAL,
            aroma REAL,
            acidity REAL,
            sweetness REAL,
            body REAL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS software_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    for statement in (
        "ALTER TABLE samples ADD COLUMN raw_ch1 REAL",
        "ALTER TABLE samples ADD COLUMN raw_ch2 REAL",
        "ALTER TABLE samples ADD COLUMN raw_ch3 REAL",
        "ALTER TABLE samples ADD COLUMN raw_ch4 REAL",
        "ALTER TABLE samples ADD COLUMN raw_payload TEXT",
        "ALTER TABLE samples ADD COLUMN source TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "ALTER TABLE samples ADD COLUMN data_source TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "ALTER TABLE samples ADD COLUMN source_port TEXT",
        "ALTER TABLE samples ADD COLUMN raw_frame TEXT",
        "ALTER TABLE samples ADD COLUMN parser_format TEXT",
        "ALTER TABLE samples ADD COLUMN temperature_unit TEXT NOT NULL DEFAULT 'C'",
        "ALTER TABLE samples ADD COLUMN channel_mapping TEXT",
        "ALTER TABLE samples ADD COLUMN source_baudrate INTEGER",
        "ALTER TABLE samples ADD COLUMN protocol_profile TEXT",
        "ALTER TABLE samples ADD COLUMN time_basis TEXT NOT NULL DEFAULT 'capture_relative'",
        "ALTER TABLE samples ADD COLUMN parse_error_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE samples ADD COLUMN timestamp REAL",
        "ALTER TABLE events ADD COLUMN it REAL",
        "ALTER TABLE events ADD COLUMN et REAL",
        "ALTER TABLE manual_actions ADD COLUMN damper_level REAL",
        "ALTER TABLE manual_actions ADD COLUMN rpm_level REAL",
        "ALTER TABLE manual_actions ADD COLUMN control_scale TEXT NOT NULL DEFAULT 'legacy'",
    ):
        try:
            cursor.execute(statement)
        except sqlite3.OperationalError:
            pass

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roast_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            caption TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (roast_id) REFERENCES roast_info(id)
        )
        """
    )

    # Session archives are intentionally separate from the legacy roast tables.
    # Existing callers may continue to use roast_info/samples/events/manual_actions,
    # while the archive API writes one immutable session snapshot transactionally.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            charge_number INTEGER NOT NULL UNIQUE,
            started_at TEXT,
            ended_at TEXT,
            duration_s REAL NOT NULL DEFAULT 0,
            drop_time_s REAL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            action_count INTEGER NOT NULL DEFAULT 0,
            samples_json TEXT NOT NULL,
            events_json TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            stage_log_json TEXT NOT NULL,
            archived_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_charges_number
        ON charges (charge_number DESC)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            charge_id INTEGER NOT NULL UNIQUE,
            batch_number INTEGER NOT NULL UNIQUE,
            coffee_name TEXT,
            bean_origin TEXT,
            charge_weight_g REAL,
            duration_s REAL NOT NULL DEFAULT 0,
            drop_bt REAL,
            fc_start_s REAL,
            gas_action_count INTEGER NOT NULL DEFAULT 0,
            damper_action_count INTEGER NOT NULL DEFAULT 0,
            rpm_action_count INTEGER NOT NULL DEFAULT 0,
            curve_snapshot_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (charge_id) REFERENCES charges(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_batches_number
        ON batches (batch_number DESC)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute("UPDATE schema_version SET version = 4 WHERE id = 1")

    conn.commit()
