import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtCore

from app.database.database import RoastDatabase
from app.device.channel_mapping import ChannelMapper
from app.device.hb_parser import HbParser
from app.device.serial_manager import SerialManager


class DataSourceAuditTests(unittest.TestCase):
    def test_json_without_device_time_uses_capture_time_and_tc4_format(self) -> None:
        parser = HbParser()
        parsed = parser.parse(
            '{"CH1": 180, "CH2": 165, "CH3": 112, "CH4": 130}',
            when=7.5,
            profile="HB_TC4",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["time_s"], 7.5)
        self.assertEqual(parsed["time_basis"], "capture_relative")
        self.assertEqual(parsed["parser_format"], "json")
        self.assertEqual(parsed["temperature_unit"], "C")

    def test_tc4_and_legacy_numeric_profiles_are_distinct(self) -> None:
        parser = HbParser()
        tc4 = parser.parse("180,165,112,130", when=4.0, profile="HB_TC4")
        legacy = parser.parse("9,180,165,112", when=4.0, profile="HB_LEGACY_3")
        tc4_three = parser.parse("180,165,112", when=4.0, profile="HB_TC4")
        tc4_error = parser.last_error
        legacy_three = parser.parse("180,165,112", when=4.0, profile="HB_LEGACY_3")
        self.assertEqual(tc4["CH4"], 130.0)
        self.assertEqual(tc4["time_s"], 4.0)
        self.assertEqual(legacy["time_s"], 9.0)
        self.assertNotIn("CH4", legacy)
        self.assertIsNone(tc4_three)
        self.assertIn("CH4", tc4_error)
        self.assertEqual(legacy_three["CH1"], 180.0)

    def test_unsupported_unit_and_incomplete_frame_are_rejected(self) -> None:
        parser = HbParser()
        self.assertIsNone(parser.parse('{"CH1": 180, "unit": "F"}', when=2.0, profile="HB_TC4"))
        mapper = ChannelMapper(Path(tempfile.mkdtemp()) / "channel.json")
        partial = parser.parse('{"CH1": 180}', when=2.0, profile="HB_TC4")
        self.assertIsNone(mapper.map_sample(partial))
        self.assertIn("缺少完整温度通道", mapper.last_error)

    def test_alias_and_configured_channel_conflict_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mapper = ChannelMapper(Path(directory) / "channel.json")
            conflict = {
                "time_s": 1.0,
                "IT_RAW": 165.0,
                "CH1": 180.0,
                "CH2": 120.0,
                "CH3": 110.0,
                "CH4": 130.0,
            }
            self.assertIsNone(mapper.map_sample(conflict))
            self.assertIn("冲突", mapper.last_error)

    def test_empty_source_does_not_start_simulation_and_explicit_simulation_is_traceable(self) -> None:
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        manager = SerialManager()
        errors = []
        samples = []
        manager.error.connect(errors.append)
        manager.sample_received.connect(samples.append)
        manager.connect("")
        app.processEvents()
        self.assertFalse(manager._simulate)
        self.assertIsNone(manager._timer)
        self.assertTrue(errors)
        self.assertEqual(samples, [])

        manager.connect("SIMULATION")
        manager._emit_sim_sample()
        self.assertEqual(samples[-1]["data_source"], "SIMULATION")
        self.assertEqual(samples[-1]["source_port"], "SIMULATION")
        self.assertEqual(samples[-1]["profile"], "SIMULATION")
        self.assertTrue(samples[-1]["raw_frame"])
        manager.disconnect()

    def test_sample_source_metadata_is_persisted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(RoastDatabase(Path(directory) / "audit.hbroast")) as db,
        ):
            batch_id = db.create_batch(device="COM7")
            db.insert_sample(
                {
                    "time_s": 1.0,
                    "it": 165.0,
                    "et": 180.0,
                    "bt": 112.0,
                    "CH1": 180.0,
                    "CH2": 165.0,
                    "CH3": 112.0,
                    "CH4": 130.0,
                    "source": "SERIAL",
                    "data_source": "SERIAL",
                    "source_port": "COM7",
                    "raw_frame": '{"CH1":180,"CH2":165,"CH3":112,"CH4":130}',
                    "parser_format": "json",
                    "temperature_unit": "C",
                    "channel_mapping": "CH1->ET,CH2->IT,CH3->BT,CH4->WORK",
                    "baudrate": 115200,
                    "profile": "HB_TC4",
                    "time_basis": "capture_relative",
                    "parse_error_count": 2,
                },
                roast_id=batch_id,
            )
            row = db.load_samples(batch_id)[0]
            self.assertEqual(row["data_source"], "SERIAL")
            self.assertEqual(row["source_port"], "COM7")
            self.assertEqual(row["source_baudrate"], 115200)
            self.assertEqual(row["protocol_profile"], "HB_TC4")
            self.assertIn("CH1", row["raw_frame"])
            self.assertEqual(row["parse_error_count"], 2)


if __name__ == "__main__":
    unittest.main()
