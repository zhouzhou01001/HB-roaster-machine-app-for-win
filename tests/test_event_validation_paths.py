import csv
import tempfile
import unittest
from pathlib import Path

from app.database.database import RoastDatabase
from app.roast.importer import import_csv_batch


class EventValidationPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self._workspace.cleanup)
        self.db_path = Path(self._workspace.name) / "events.hbroast"
        self.db = RoastDatabase(self.db_path)
        self.addCleanup(self.db.close)

    def test_db_rejects_duplicate_and_out_of_order_events(self) -> None:
        batch_id = self.db.create_batch(coffee_name="validation")
        first = self.db.insert_event(batch_id, 10.0, "CHARGE", 95.0)
        duplicate = self.db.insert_event(batch_id, 10.0, "CHARGE", 95.0)
        self.assertEqual(first, duplicate)
        self.assertEqual(len(self.db.load_events(batch_id)), 1)

        self.db.insert_event(batch_id, 20.0, "YELLOW", 105.0)
        with self.assertRaises(ValueError):
            self.db.insert_event(batch_id, 15.0, "FC_START", 102.0)

    def test_importer_rejects_out_of_order_events_and_allows_legal_sequence(self) -> None:
        bad_csv = Path(self._workspace.name) / "bad_events.csv"
        with bad_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Time", "IT", "ET", "BT", "Event"])
            writer.writerow([0, 100, 110, 90, "CHARGE"])
            writer.writerow([20, 110, 120, 96, "YELLOW"])
            writer.writerow([10, 110, 120, 97, "FC_START"])
        with self.assertRaises(ValueError):
            import_csv_batch(self.db, bad_csv)
        self.assertEqual(len(self.db.list_roasts()), 1)

        good_csv = Path(self._workspace.name) / "good_events.csv"
        with good_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Time", "IT", "ET", "BT", "Event"])
            writer.writerow([0, 100, 110, 90, "CHARGE"])
            writer.writerow([20, 110, 120, 96, "YELLOW"])
            writer.writerow([40, 120, 130, 102, "FC_START"])
            writer.writerow([60, 130, 140, 104, "DROP"])
        batch_id = import_csv_batch(self.db, good_csv)
        events = self.db.load_events(batch_id)
        self.assertEqual([row["event_type"] for row in events], ["CHARGE", "YELLOW", "FC_START", "DROP"])
        self.assertEqual([row["time_s"] for row in events], [0.0, 20.0, 40.0, 60.0])

    def test_csv_import_midway_validation_failure_is_atomic(self) -> None:
        # Prepare an active batch to verify current batch pointer restore.
        active_batch = self.db.create_batch(coffee_name="active")
        self.assertEqual(self.db.current_roast_id, active_batch)
        pre_ids = {row["id"] for row in self.db.list_roasts()}

        malformed_csv = Path(self._workspace.name) / "malformed_events.csv"
        with malformed_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Time", "IT", "ET", "BT", "Event"])
            writer.writerow([0, 100, 110, 90, "CHARGE"])
            writer.writerow([20, 110, 120, 96, "YELLOW"])
            writer.writerow([10, 120, 130, 97, "FC_START"])

        with self.assertRaisesRegex(
            ValueError,
            r"CSV row\s+4,\s+event\s+FC_START",
        ) as err:
            import_csv_batch(self.db, malformed_csv)

        post_ids = {row["id"] for row in self.db.list_roasts()}
        self.assertEqual(self.db.current_roast_id, active_batch)
        self.assertEqual(len(post_ids) - len(pre_ids), 1)
        failed_import_ids = list(post_ids - pre_ids)
        self.assertEqual(len(failed_import_ids), 1)
        failed_batch_id = failed_import_ids[0]

        self.assertEqual(self.db.load_samples(failed_batch_id), [])
        self.assertEqual(self.db.load_events(failed_batch_id), [])
        self.assertIn("CSV row", str(err.exception))
