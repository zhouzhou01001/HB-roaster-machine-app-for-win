from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from app.database.database import RoastDatabase
from app.device.channel_mapping import ChannelMapper
from app.device.serial_manager import SerialManager
from app.roast.sample_quality import SampleQualityGate
from app.roast.reference_profile import load_reference_profile, save_profile_from_batch


class ReviewDeviceRegressions(unittest.TestCase):
    def test_reconnect_keeps_device_relative_clock_reset(self):
        manager = SerialManager()
        port = Mock(baudrate=115200)
        with patch("app.device.serial_manager.serial") as serial, patch("app.device.serial_manager.threading.Thread"), patch("app.device.serial_manager.time.time", side_effect=[10.0, 30.0]):
            serial.serial_for_url.return_value = port
            manager.connect("COM_TEST", profile="HB_MODEL_S")
            self.assertEqual(manager._base_time, 10.0)
            manager.disconnect()
            manager.connect("COM_TEST", profile="HB_MODEL_S")
            self.assertEqual(manager._base_time, 30.0)
            manager.disconnect()

    def test_second_chan_failure_discards_the_first_pair(self):
        with patch("app.device.serial_manager.time.sleep"):
            manager = SerialManager()
            manager._profile = "HB_MODEL_S"
            manager._serial = Mock()
            manager._serial.readline.side_effect = [b"#OK\n", b"25,130,165\n", b"ERROR\n"]
            samples = []
            manager.sample_received.connect(samples.append)
            manager._read_model_s_sample()
            self.assertEqual(samples, [])
            self.assertEqual([call.args[0] for call in manager._serial.write.call_args_list],
                             [b"CHAN;1200\n", b"READ\n", b"CHAN;3400\n"])

    def test_chan_failure_never_reads_or_emits_a_sample(self):
        for ack in (b"", b"ERROR\n", b"\n", b"#OK"):
            with self.subTest(ack=ack), patch("app.device.serial_manager.time.sleep"):
                manager = SerialManager()
                manager._profile = "HB_MODEL_S"
                manager._serial = Mock()
                manager._serial.readline.side_effect = [ack, b"20,100,150\n"]
                samples = []
                manager.sample_received.connect(samples.append)
                manager._read_model_s_sample()
                self.assertEqual(samples, [])
                self.assertEqual(manager.valid_frame_count, 0)
                self.assertEqual(manager.parse_error_count, 1)
                manager._serial.write.assert_called_once_with(b"CHAN;1200\n")

    def test_model_s_confirmed_physical_order_is_preserved(self):
        with patch("app.device.serial_manager.time.sleep"):
            manager = SerialManager()
            manager._profile = "HB_MODEL_S"
            manager._serial = Mock()
            manager._serial.readline.side_effect = [
                b"#OK\n", b"25,130,165\n", b"#OK\n", b"25,112,180\n"
            ]
            samples = []
            manager.sample_received.connect(samples.append)
            manager._read_model_s_sample()
            self.assertEqual(len(samples), 1)
            with tempfile.TemporaryDirectory() as directory:
                mapper = ChannelMapper(Path(directory) / "channels.json")
                mapped = mapper.map_sample(samples[0])
            self.assertEqual({key: mapped[key] for key in ("it", "et", "bt", "work")},
                             {"it": 165.0, "et": 180.0, "bt": 112.0, "work": 130.0})

    def test_model_s_nonfinite_pair_is_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), patch("app.device.serial_manager.time.sleep"):
                manager = SerialManager()
                manager._serial = Mock()
                manager._serial.readline.side_effect = [b"#OK\n", f"25,{value},180\n".encode()]
                self.assertIsNone(manager._request_tc4_pair("1200"))

    def test_mapping_rejects_duplicates_without_replacing_saved_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            mapper = ChannelMapper(Path(directory) / "channels.json")
            original = dict(mapper.mapping)
            for mapping in (
                {"CH1": "ET", "CH2": "IT", "CH3": "BT", "CH4": "BT"},
                {"CH1": "ET", "ch1": "WORK", "CH2": "IT", "CH3": "BT"},
            ):
                with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                    mapper.set_mapping(mapping)
            self.assertEqual(mapper.mapping, original)
            self.assertEqual(json.loads(mapper.config_path.read_text(encoding="utf-8")), original)
            mapper.mapping = {"CH1": "ET", "CH2": "IT", "CH3": "BT", "CH4": "BT"}
            self.assertFalse(mapper.is_complete)

    def test_legacy_three_channel_mapping_adds_only_unbound_fourth_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            original = {"CH1": "IT", "CH2": "ET", "CH3": "BT"}
            path.write_text(json.dumps(original), encoding="utf-8")
            mapper = ChannelMapper(path)
            self.assertEqual(mapper.mapping, {**original, "CH4": "WORK"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            mapper.set_mapping({**original, "CH4": "AMBIENT"})
            self.assertEqual(mapper.mapping["CH4"], "AMBIENT")

    def test_duplicate_loaded_mapping_is_rejected_without_rewriting_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            original = {"CH1": "ET", "CH2": "IT", "CH3": "BT", "CH4": "BT"}
            path.write_text(json.dumps(original), encoding="utf-8")
            mapper = ChannelMapper(path)
            self.assertFalse(mapper.is_complete)
            self.assertIsNone(mapper.map_sample({"time_s": 1, "CH1": 100, "CH2": 110, "CH3": 120, "CH4": 130}))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_invalid_times_do_not_poison_quality_gate_or_raise_in_mapper(self):
        with tempfile.TemporaryDirectory() as directory:
            mapper = ChannelMapper(Path(directory) / "channels.json")
            for value in (float("nan"), float("inf"), -float("inf"), "invalid", None):
                with self.subTest(value=value):
                    gate = SampleQualityGate()
                    self.assertTrue(gate.accept({"time_s": 1, "bt": 100}).accepted)
                    self.assertFalse(gate.accept({"time_s": value, "bt": 100}).accepted)
                    self.assertTrue(gate.accept({"time_s": 2, "bt": 101}).accepted)
                    self.assertIsNone(mapper.map_sample({"time_s": value, "CH1": 120, "CH2": 130, "CH3": 100}))


class ReviewArchiveRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.hbroast"
        self.db = RoastDatabase(self.path)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def test_archive_rejects_invalid_rows_atomically(self):
        valid = [{"time_s": 0.0, "bt": 100.0}, {"time_s": 2.0, "bt": 102.0}]
        cases = [
            ([], [], []),
            ([{"bt": 100}], [], []),
            ([{"time_s": float("nan"), "bt": 100}], [], []),
            ([{"time_s": float("inf"), "bt": 100}], [], []),
            ([{"time_s": -1, "bt": 100}], [], []),
            (list(reversed(valid)), [], []),
            ([valid[0], valid[0]], [], []),
            ([{"time_s": 0, "bt": float("inf")}], [], []),
            (valid, [{"time_s": 1, "event_type": "UNKNOWN"}], []),
            (valid, [{"time_s": 0, "event_type": "DROP"}, {"time_s": 1, "event_type": "CHARGE"}], []),
            (valid, [{"time_s": 1, "event_type": "DROP"}] * 2, []),
            (valid, [], [{"time_s": float("inf"), "gas_mbar": 10}]),
            (valid, [], [{"time_s": 1, "damper_level": 11}]),
        ]
        for samples, events, actions in cases:
            with self.subTest(samples=samples, events=events, actions=actions):
                with self.assertRaises(ValueError):
                    self.db.archive_session(samples, events, actions)
                self.assertEqual(self.db.list_batches(), [])
                self.assertEqual(self.db.list_charges(), [])

    def test_pre_archive_database_migrates_and_reads_without_losing_legacy_data(self):
        old_path = Path(self.directory.name) / "old.hbroast"
        with closing(sqlite3.connect(old_path)) as conn:
            conn.executescript("""
                CREATE TABLE roast_info (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,
                    device TEXT, operator TEXT, coffee_name TEXT, green_weight REAL, roasted_weight REAL);
                CREATE TABLE samples (id INTEGER PRIMARY KEY AUTOINCREMENT, roast_id INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL, time_s REAL NOT NULL, it REAL, et REAL, bt REAL,
                    it_ror REAL, et_ror REAL, bt_ror REAL);
                INSERT INTO roast_info (id, coffee_name) VALUES (7, 'old schema');
                INSERT INTO samples (roast_id, time_ms, time_s, bt) VALUES (7, 1000, 1, 123);
            """)
        old = RoastDatabase(old_path)
        try:
            self.assertEqual(old.load_history_session(7, source="legacy")["samples"][0]["bt"], 123)
            archive = old.archive_session([{"time_s": 1, "bt": 222}], [], [])
            self.assertEqual(old.load_history_session(archive.batch_id, source="archive")["samples"][0]["bt"], 222)
            old.insert_sample({"time_s": 2, "bt": 124}, roast_id=7)
            self.assertEqual(len(old.load_history_session(7, source="legacy")["samples"]), 2)
        finally:
            old.close()

    def test_archive_rejects_invalid_metadata_and_fractional_numbers(self):
        cases = [
            {"duration_s": float("nan")}, {"duration_s": float("inf")},
            {"duration_s": 0}, {"drop_time_s": -1}, {"fc_start_s": float("inf")},
            {"charge_weight_g": -1}, {"drop_bt": float("inf")},
            {"charge_number": 1.5}, {"batch_number": 2.5},
            {"gas_action_count": 1.2}, {"damper_action_count": True},
            {"stage_log": [{"time_s": float("nan")}]},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.db.archive_session([{"time_s": 1, "bt": 100}], [], [], metadata)
        for number in (True, 0, 1.5, float("inf")):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.db.archive_session([{"time_s": 1, "bt": 100}], [], [], charge_number=number)
        self.assertEqual(self.db.list_charges(), [])
        self.assertEqual(self.db.list_batches(), [])

    def test_unified_history_keeps_same_id_sources_separate_and_survives_reopen(self):
        legacy_id = self.db.create_batch(coffee_name="legacy")
        self.db.insert_sample({"time_s": 1, "bt": 100}, roast_id=legacy_id)
        self.db.insert_event(legacy_id, 1, "CHARGE", bt=100)
        self.db.insert_manual_action(legacy_id, 1, 1.8, 3, 5)
        sample = {"time_s": 1, "bt": 200, "work": 210, "gap_before": True}
        archived = self.db.archive_session([sample], [{"time_s": 1, "event_type": "DROP"}],
                                          [{"time_s": 1, "gas_mbar": 18}], {"coffee_name": "archive"})
        self.assertEqual(legacy_id, archived.batch_id)
        self.db.close()
        self.db = RoastDatabase(self.path)
        changes = self.db.conn.total_changes
        legacy = self.db.load_history_session(legacy_id, source="legacy")
        archive = self.db.load_history_session(archived.batch_id, source="archive")
        self.assertEqual(legacy["samples"][0]["bt"], 100)
        self.assertEqual(archive["samples"], [sample])
        self.assertEqual(legacy["metadata"]["coffee_name"], "legacy")
        self.assertEqual(archive["metadata"]["coffee_name"], "archive")
        self.assertEqual(len(legacy["actions"]), 1)
        self.assertEqual(len(archive["events"]), 1)
        self.assertEqual({(row["source"], row["id"]) for row in self.db.list_history_sessions()},
                         {("legacy", legacy_id), ("archive", archived.batch_id)})
        self.assertIsNone(self.db.load_history_session(999, source="archive"))
        self.assertIsNone(self.db.load_history_session(999, source="legacy"))
        with self.assertRaises(ValueError):
            self.db.load_history_session(legacy_id, source="auto")
        self.assertEqual(self.db.conn.total_changes, changes)

    def test_archive_reference_uses_null_legacy_fk_and_preserves_source_data(self):
        legacy = self.db.create_batch(coffee_name="same id legacy")
        self.db.insert_sample({"time_s": 1, "bt": 99}, roast_id=legacy)
        sample = {"time_s": 1, "bt": 201, "work": 220, "gap_before": True}
        archive = self.db.archive_session([sample], [{"time_s": 1, "event_type": "DROP"}],
                                          [{"time_s": 1, "gas_mbar": 18, "damper_level": 3, "rpm_level": 5}],
                                          {"coffee_name": "archive beans", "charge_weight_g": 350})
        self.assertEqual(legacy, archive.batch_id)
        self.assertFalse(next(row for row in self.db.conn.execute("PRAGMA table_info(profiles)")
                              if row["name"] == "roast_id")["notnull"])
        self.db.conn.execute("PRAGMA foreign_keys=ON")
        profile_id = save_profile_from_batch(self.db, archive.batch_id, "archive reference", source="archive")
        profile = load_reference_profile(self.db, profile_id)
        self.assertIsNone(profile["roast_id"])
        self.assertIsNone(profile["payload"]["roast_id"])
        self.assertEqual(profile["payload"]["source"], "archive")
        self.assertEqual(profile["payload"]["batch_id"], archive.batch_id)
        self.assertEqual(profile["payload"]["samples"], [sample])
        self.assertEqual(profile["payload"]["manual_actions"][0]["gas_kpa"], 1.8)
        self.assertEqual(self.db.list_roast_ids(), [legacy])
        legacy_profile = load_reference_profile(self.db, save_profile_from_batch(self.db, legacy, "legacy reference"))
        self.assertEqual(legacy_profile["roast_id"], legacy)
        self.assertEqual(legacy_profile["payload"]["samples"][0]["bt"], 99)
        with self.assertRaises(ValueError):
            save_profile_from_batch(self.db, 999, "missing", source="archive")

    def test_archive_metadata_patch_updates_both_stores_and_reference(self):
        legacy = self.db.create_batch(coffee_name="do not change", green_weight=111)
        archive = self.db.archive_session([{"time_s": 1, "bt": 201}], [], [],
                                          {"coffee_name": "old", "charge_weight_g": 350,
                                           "bean_origin": "old origin", "custom": "keep", "stage_log": [{"to": "between"}]})
        before = self.db.load_charge(archive.charge_id)
        self.db.update_history_metadata(archive.batch_id, {"device": "COM7", "operator": "operator",
                                        "coffee_name": "new", "green_weight": 400, "roasted_weight": 340,
                                        "origin": "new origin"}, source="archive")
        charge = self.db.load_charge(archive.charge_id)
        batch = self.db.load_batch(archive.batch_id)
        for metadata in (charge["metadata"], batch["metadata"]):
            self.assertEqual(metadata["coffee_name"], "new")
            self.assertEqual(metadata["green_weight"], 400)
            self.assertEqual(metadata["charge_weight_g"], 400)
            self.assertEqual(metadata["roasted_weight"], 340)
            self.assertEqual(metadata["bean_origin"], "new origin")
            self.assertEqual(metadata["custom"], "keep")
        self.assertEqual(batch["coffee_name"], "new")
        self.assertEqual(batch["charge_weight_g"], 400)
        self.assertEqual(batch["bean_origin"], "new origin")
        for key in ("samples", "events", "actions", "stage_log", "sample_count", "duration_s"):
            self.assertEqual(charge[key], before[key])
        self.assertEqual(self.db.load_history_session(legacy, source="legacy")["metadata"]["coffee_name"], "do not change")
        profile = load_reference_profile(self.db, save_profile_from_batch(self.db, archive.batch_id, "edited", source="archive"))
        self.assertEqual(profile["payload"]["batch_info"]["green_weight"], 400)
        self.db.update_history_metadata(archive.batch_id, {"green_weight": None, "coffee_name": ""}, source="archive")
        self.assertIsNone(self.db.list_batches()[0]["charge_weight_g"])
        self.assertEqual(self.db.list_batches()[0]["coffee_name"], "")

    def test_archive_metadata_failure_rolls_back_both_updates(self):
        archive = self.db.archive_session([{"time_s": 1, "bt": 100}], [], [], {"coffee_name": "original"})
        before_charge = self.db.load_charge(archive.charge_id)
        before_batch = self.db.load_batch(archive.batch_id)
        self.db.conn.execute("""CREATE TRIGGER fail_metadata BEFORE UPDATE ON batches
                              BEGIN SELECT RAISE(ABORT, 'forced metadata failure'); END""")
        self.db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.update_history_metadata(archive.batch_id, {"coffee_name": "failed"}, source="archive")
        self.assertEqual(self.db.load_charge(archive.charge_id), before_charge)
        self.assertEqual(self.db.load_batch(archive.batch_id), before_batch)

    def test_history_metadata_legacy_contract_and_archive_validation(self):
        legacy = self.db.create_batch(device="old", operator="old", coffee_name="old", green_weight=100)
        self.db.update_history_metadata(legacy, {"coffee_name": "legacy edited"}, source="legacy")
        metadata = self.db.load_history_session(legacy, source="legacy")["metadata"]
        self.assertEqual(metadata["coffee_name"], "legacy edited")
        self.assertEqual(metadata["device"], "")
        self.assertIsNone(metadata["green_weight"])
        archive = self.db.archive_session([{"time_s": 1, "bt": 100}], [], [], {"charge_weight_g": 350})
        self.assertEqual(self.db.load_history_session(archive.batch_id, source="archive")["metadata"]["green_weight"], 350)
        for payload in ({"green_weight": -1}, {"green_weight": float("nan")},
                        {"roasted_weight": float("inf")}, {"green_weight": 200, "charge_weight_g": 300}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.db.update_history_metadata(archive.batch_id, payload, source="archive")
        with self.assertRaises(ValueError):
            self.db.update_history_metadata(999, {"coffee_name": "missing"}, source="archive")
        with self.assertRaises(ValueError):
            self.db.update_history_metadata(legacy, {}, source="auto")
        self.assertEqual(self.db.load_batch(archive.batch_id)["charge_weight_g"], 350)


if __name__ == "__main__":
    unittest.main()
