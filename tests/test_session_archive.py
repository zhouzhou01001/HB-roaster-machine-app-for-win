from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.database import RoastDatabase, SessionArchiveResult


class SessionArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "archive.hbroast"
        self.db = RoastDatabase(self.db_path)

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_archive_session_writes_charge_and_batch_once_and_round_trips(self) -> None:
        samples = [
            {"time_s": 0.0, "et": 180.0, "bt": 95.0, "it": 170.0},
            {"time_s": 60.0, "et": 205.0, "bt": 150.0, "it": 190.0},
            {"time_s": 120.0, "et": 220.0, "bt": 205.0, "it": 210.0},
        ]
        events = [
            {"event_type": "CHARGE", "time_s": 0.0, "bt": 95.0},
            {"event_type": "FC_START", "time_s": 90.0, "bt": 190.0},
            {"event_type": "DROP", "time_s": 120.0, "bt": 205.0},
        ]
        actions = [
            {"time_s": 0.0, "gas_mbar": 18.0, "damper_level": 3, "rpm_level": 5},
            {"time_s": 60.0, "gas_mbar": 14.0, "damper_level": 5, "rpm_level": 6},
        ]
        metadata = {
            "coffee_name": "日晒耶加",
            "bean_origin": "埃塞俄比亚",
            "charge_weight_g": 350.0,
            "started_at": "2026-08-27T10:00:00",
            "ended_at": "2026-08-27T10:02:00",
            "stage_log": [
                {"from": "idle", "to": "roasting"},
                {"from": "roasting", "to": "await_end"},
                {"from": "await_end", "to": "between"},
            ],
        }

        result = self.db.archive_session(samples, events, actions, metadata)

        self.assertIsInstance(result, SessionArchiveResult)
        self.assertEqual(result.charge_number, 1)
        self.assertEqual(len(self.db.list_charges()), 1)
        self.assertEqual(len(self.db.list_batches()), 1)

        charge = self.db.load_charge(result.charge_id)
        self.assertIsNotNone(charge)
        self.assertEqual(charge["sample_count"], 3)
        self.assertEqual(charge["event_count"], 3)
        self.assertEqual(charge["action_count"], 2)
        self.assertEqual(charge["drop_time_s"], 120.0)
        self.assertEqual(charge["samples"], samples)
        self.assertEqual(charge["events"], events)
        self.assertEqual(charge["actions"], actions)
        self.assertEqual(charge["metadata"]["coffee_name"], "日晒耶加")
        self.assertEqual(charge["stage_log"][-1]["to"], "between")

        batch = self.db.load_batch(result.batch_id)
        self.assertIsNotNone(batch)
        self.assertEqual(batch["charge_id"], result.charge_id)
        self.assertEqual(batch["batch_number"], 1)
        self.assertEqual(batch["duration_s"], 120.0)
        self.assertEqual(batch["drop_bt"], 205.0)
        self.assertEqual(batch["fc_start_s"], 90.0)
        self.assertEqual(batch["coffee_name"], "日晒耶加")
        self.assertEqual(batch["curve_snapshot"]["bt"], [95.0, 150.0, 205.0])

    def test_archive_session_rolls_back_both_tables_when_batch_insert_fails(self) -> None:
        self.db.conn.execute(
            """
            CREATE TRIGGER fail_batch_archive
            BEFORE INSERT ON batches
            BEGIN
                SELECT RAISE(ABORT, 'forced batch archive failure');
            END
            """
        )
        self.db.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.archive_session(
                [{"time_s": 1.0, "bt": 100.0}],
                [{"event_type": "DROP", "time_s": 1.0, "bt": 100.0}],
                [],
                {"coffee_name": "rollback"},
                charge_number=7,
            )

        self.assertEqual(self.db.list_charges(), [])
        self.assertEqual(self.db.list_batches(), [])

    def test_config_values_are_typed_and_upserted(self) -> None:
        self.assertEqual(self.db.get_config_value("missing", {"default": True}), {"default": True})

        self.db.set_config_value("sampling", {"hz": 2, "enabled": True})
        self.db.set_config_value("theme", "dark")
        self.db.set_config_value("sampling", {"hz": 4, "enabled": False})

        self.assertEqual(
            self.db.get_config_value("sampling"),
            {"hz": 4, "enabled": False},
        )
        self.assertEqual(
            self.db.list_config_values(),
            {
                "sampling": {"hz": 4, "enabled": False},
                "theme": "dark",
            },
        )
        with self.assertRaises(ValueError):
            self.db.set_config_value("  ", 1)

    def test_legacy_tables_remain_compatible_and_separate_from_archives(self) -> None:
        roast_id = self.db.create_batch(coffee_name="legacy")
        self.db.insert_sample(
            {"time_s": 1.0, "time_ms": 1000, "it": 120.0, "et": 130.0, "bt": 100.0},
            roast_id=roast_id,
        )
        self.db.insert_event(roast_id, 1.0, "CHARGE", bt=100.0)
        self.db.insert_manual_action(roast_id, 1.0, 1.8, 3.0, 5.0, "legacy action")

        self.assertEqual(len(self.db.load_samples(roast_id)), 1)
        self.assertEqual(len(self.db.load_events(roast_id)), 1)
        self.assertEqual(len(self.db.load_manual_actions(roast_id)), 1)
        self.assertEqual(self.db.list_charges(), [])
        self.assertEqual(self.db.list_batches(), [])

        archive = self.db.archive_session(
            [{"time_s": 2.0, "bt": 110.0}],
            [{"event_type": "DROP", "time_s": 2.0, "bt": 110.0}],
            [],
            {"coffee_name": "new archive"},
        )
        self.assertEqual(archive.charge_number, 1)
        self.assertEqual(len(self.db.load_samples(roast_id)), 1)
        self.assertEqual(len(self.db.load_events(roast_id)), 1)
        self.assertEqual(len(self.db.load_manual_actions(roast_id)), 1)


if __name__ == "__main__":
    unittest.main()
