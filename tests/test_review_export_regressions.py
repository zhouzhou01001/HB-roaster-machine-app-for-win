from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import app.export as exports
from app.export import csv_export
from app.export.chart_pdf import _draw_series, export_chart_pdf
from app.export.json_export import export_roast_json
from app.database.database import RoastDatabase
from app.roast import importer
from app.ui.curve_widget import CURVE_COLORS, CurveWidget


class ReviewCurveRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.chart = CurveWidget()
        self.addCleanup(self.chart.close)

    @staticmethod
    def samples():
        return [
            dict(time_s=t, it=100+i, et=110+i, bt=90+i, work=120+i,
                 it_ror=1+i, et_ror=2+i, bt_ror=3+i, work_ror=4+i,
                 gap_before=i == 2)
            for i, t in enumerate((0, 1, 20, 21))
        ]

    def test_fourth_channel_ror_style_axis_and_controls(self):
        self.chart.set_data(self.samples())
        curves = {c["key"]: c for c in self.chart.export_snapshot()["curves"]}
        self.assertIn("EXHAUST_ROR", curves)
        self.assertEqual(curves["EXHAUST_ROR"]["points"], [(0., 4.), (1., 5.), (20., 6.), (21., 7.)])
        self.assertEqual(curves["EXHAUST_ROR"]["axis"], "right")
        self.assertEqual(curves["EXHAUST"]["axis"], "left")
        self.assertEqual(curves["EXHAUST_ROR"]["color"], CURVE_COLORS["exhaust"])
        self.assertTrue(curves["EXHAUST_ROR"]["dashed"])
        self.chart.set_curve_visibility("EXHAUST_ROR", False)
        self.assertFalse(self.chart.exhaust_ror_curve.isVisible())
        self.assertTrue(self.chart.exhaust_curve.isVisible())
        self.chart.set_curve_line_width("EXHAUST_ROR", 1.8)
        self.assertAlmostEqual(self.chart.exhaust_ror_curve.opts["pen"].widthF(), 1.8)
        self.chart.set_theme(False)
        self.assertEqual(self.chart.exhaust_ror_curve.opts["pen"].color().name(), CURVE_COLORS["exhaust"])

    def test_gap_mask_breaks_all_live_and_background_curves(self):
        rows = self.samples()
        self.chart.set_data(rows)
        self.chart.set_background_profile(rows)
        keys = ("IT", "ET", "BT", "EXHAUST", "IT_ROR", "ET_ROR", "BT_ROR", "EXHAUST_ROR", "BG_BT", "BG_ROR")
        for key in keys:
            with self.subTest(key=key):
                item = self.chart._curve_items[key]
                self.assertIsInstance(item.opts["connect"], np.ndarray)
                self.assertEqual(item.opts["connect"].tolist(), [True, False, True, False])
                path = item.curve.getPath()
                edges = [(path.elementAt(i).x, path.elementAt(i-1).x)
                         for i in range(1, path.elementCount())
                         if path.elementAt(i).type == QtGui.QPainterPath.ElementType.LineToElement]
                self.assertEqual(edges, [(1., 0.), (21., 20.)])
                self.assertEqual(len(item.getData()[0]), 4)
        self.assertEqual(rows, self.samples())

    def test_missing_values_do_not_bridge_and_legacy_fourth_channel_works(self):
        self.chart.set_data([
            {"time_s": 0, "work": 120, "work_ror": 4},
            {"time_s": 1, "work": None},
            {"time_s": 2, "work": None, "CH4": 122, "WORK_RoR": 6},
            {"time_s": 3, "work": 123, "work_ror": 0},
        ])
        curves = {c["key"]: c for c in self.chart.export_snapshot()["curves"]}
        self.assertEqual(curves["EXHAUST"]["points"], [(0., 120.), (2., 122.), (3., 123.)])
        self.assertEqual(curves["EXHAUST"]["connect"], [False, True, False])
        self.assertEqual(curves["EXHAUST_ROR"]["points"], [(0., 4.), (2., 6.), (3., 0.)])
        self.chart.set_data([])
        for key in ("EXHAUST", "EXHAUST_ROR"):
            self.assertIsNone(self.chart._curve_items[key].getData()[0])

    def test_snapshot_and_pdf_keep_reconnect_gap(self):
        self.chart.set_data(self.samples())
        curve = next(c for c in self.chart.export_snapshot()["curves"] if c["key"] == "BT")
        self.assertEqual(curve["connect"], [True, False, True, False])

        class Painter:
            def setPen(self, pen):
                pass

            def drawPath(self, path):
                self.path = path

        painter = Painter()
        _draw_series(painter, [(0, 0), (1, 1), (20, 2), (21, 3)],
                     (0, 21), (0, 3), QtCore.QRectF(0, 0, 21, 3),
                     "#000000", 1, False, connect=curve["connect"])
        self.assertEqual(sum(painter.path.elementAt(i).type == QtGui.QPainterPath.ElementType.MoveToElement
                             for i in range(painter.path.elementCount())), 2)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "curve.pdf"
            export_chart_pdf(target, self.chart)
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))

    def test_imported_database_rows_display_fourth_channel_and_background_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            db = RoastDatabase(Path(folder) / "curves.hbroast")
            try:
                source = Path(folder) / "curves.csv"
                csv_export.export_samples(self.samples(), source)
                batch_id = importer.import_csv_batch(db, source)
                rows = db.load_samples(batch_id)
                self.chart.set_data(rows)
                self.chart.set_background_profile(rows)
                curves = {c["key"]: c for c in self.chart.export_snapshot()["curves"]}
                self.assertEqual(curves["EXHAUST"]["points"], [(0., 120.), (1., 121.), (20., 122.), (21., 123.)])
                self.assertEqual(curves["EXHAUST_ROR"]["points"], [(0., 4.), (1., 5.), (20., 6.), (21., 7.)])
                for key in ("EXHAUST", "EXHAUST_ROR", "BG_BT", "BG_ROR"):
                    self.assertEqual(curves[key]["connect"], [True, False, True, False])
            finally:
                db.close()


class ReviewExportRegressions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_csv_fourth_channel_provenance_and_gap_fields(self):
        target = self.folder / "samples.csv"
        csv_export.export_samples(iter([
            {"time_s": 1, "work": 123.5, "work_ror": 6.5, "gap_before": True, "source": "SERIAL", "raw_frame": "a,b\nframe"},
            {"time_s": 2, "work": None, "CH4": 124.5, "WORK_RoR": 0},
        ]), target)
        with target.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            fields = reader.fieldnames
        self.assertIn("work", fields)
        self.assertEqual(rows[0]["work_ror"], "6.5")
        self.assertEqual(rows[0]["gap_before"], "True")
        self.assertEqual(rows[0]["raw_frame"], "a,b\nframe")
        self.assertEqual(rows[1]["work"], "124.5")
        self.assertEqual(rows[1]["work_ror"], "0")
        self.assertEqual(tuple(fields), csv_export.SAMPLE_FIELDS)
        self.assertIn("source_port", fields)

    def test_csv_empty_export_preserves_legacy_api(self):
        target = self.folder / "empty.csv"
        self.assertIsNone(csv_export.export_samples([], target))
        self.assertEqual(target.read_bytes(), b"")

    def test_csv_appends_effective_controls_and_capture_times(self):
        fields = ("elapsed_time", "gas_mbar", "gas_kpa", "damper_level", "rpm_level",
                  "control_scale", "device_time_s", "capture_time_s")
        sample = dict(zip(fields, (12.5, 25.0, 2.5, 0.0, 7.0, "0-10", 902.5, 12.5)))
        target = self.folder / "controls.csv"
        csv_export.export_samples([sample], target)
        with target.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            row = next(reader)
            self.assertEqual(reader.fieldnames[-8:], list(fields))
            self.assertEqual(reader.fieldnames[20:23], ["work", "work_ror", "gap_before"])
        self.assertEqual({key: row[key] for key in fields}, {key: str(sample[key]) for key in fields})

    def test_csv_database_round_trip_preserves_four_channels_and_controls(self):
        db = RoastDatabase(self.folder / "roundtrip.hbroast")
        self.addCleanup(db.close)
        sample = {
            "timestamp": "2026-09-12T12:00:00", "time_s": 12.5, "time_ms": 12500,
            "it": 100.0, "et": 110.0, "bt": 90.0, "work": 120.5,
            "it_ror": 1.0, "et_ror": 2.0, "bt_ror": 3.0, "work_ror": 4.5,
            "gap_before": True, "elapsed_time": 12.5, "gas_mbar": 25.0,
            "gas_kpa": 2.5, "damper_level": 0.0, "rpm_level": 7.0,
            "control_scale": "0-10", "device_time_s": 902.5, "capture_time_s": 12.5,
        }
        first, second = self.folder / "first.csv", self.folder / "second.csv"
        csv_export.export_samples([sample], first)
        batch_id = importer.import_csv_batch(db, first)
        stored = db.load_samples(batch_id)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["time_s"], 12.5)
        self.assertEqual(stored[0]["raw_ch4"], 120.5)
        csv_export.export_samples(stored, second)
        with second.open(newline="", encoding="utf-8") as stream:
            exported = next(csv.DictReader(stream))
        for key, expected in sample.items():
            with self.subTest(field=key):
                self.assertEqual(exported[key], str(expected))
        restored = importer.restore_imported_sample(stored[0])
        self.assertEqual(restored["work"], 120.5)
        self.assertEqual(restored["work_ror"], 4.5)
        self.assertIs(restored["gap_before"], True)
        self.assertNotIn("work", stored[0])

    def test_csv_import_retains_fourth_channel_only_and_legacy_aliases(self):
        db = RoastDatabase(self.folder / "legacy.hbroast")
        self.addCleanup(db.close)
        source = self.folder / "legacy.csv"
        source.write_text("Time;CH4;WORK_RoR;gap_before\n01:02;123.5;0;False\n01:03;124.5;6;True\n", encoding="utf-8")
        batch_id = importer.import_csv_batch(db, source)
        stored = db.load_samples(batch_id)
        self.assertEqual(len(stored), 2)
        restored = [importer.restore_imported_sample(row) for row in stored]
        self.assertEqual([row["time_s"] for row in restored], [62.0, 63.0])
        self.assertEqual([row["work"] for row in restored], [123.5, 124.5])
        self.assertEqual([row["work_ror"] for row in restored], [0.0, 6.0])
        self.assertEqual([row["gap_before"] for row in restored], [False, True])

    def test_csv_import_elapsed_and_millisecond_time_fallbacks(self):
        db = RoastDatabase(self.folder / "time.hbroast")
        self.addCleanup(db.close)
        source = self.folder / "time.csv"
        source.write_text("time_s,elapsed_time,time_ms,work\n,17.5,17500,123\n,,19250,124\n", encoding="utf-8")
        batch_id = importer.import_csv_batch(db, source)
        self.assertEqual([row["time_s"] for row in db.load_samples(batch_id)], [17.5, 19.25])

    def atomic(self, jobs):
        self.assertTrue(callable(getattr(exports, "export_atomic", None)), "missing atomic export API")
        return exports.export_atomic(jobs)

    def test_atomic_success_stages_every_writer_before_publishing(self):
        first, second = self.folder / "a.csv", self.folder / "b.json"
        first.write_bytes(b"old csv")
        second.write_bytes(b"old json")

        def csv_writer(stage):
            self.assertEqual(stage.suffix, ".csv")
            self.assertNotEqual(stage, first)
            csv_export.export_samples([{"time_s": 1, "work": 120}], stage)

        def json_writer(stage):
            self.assertEqual(first.read_bytes(), b"old csv")
            self.assertEqual(second.read_bytes(), b"old json")
            export_roast_json({}, [{"work": 120}], [], [], stage)

        result = self.atomic({first: csv_writer, second: json_writer})
        self.assertEqual(result, (first, second))
        self.assertIn("work", first.read_text())
        self.assertEqual(json.loads(second.read_text())["samples"], [{"work": 120}])
        self.assertEqual(set(self.folder.iterdir()), {first, second})

    def test_staging_failure_preserves_existing_and_new_targets(self):
        first, second = self.folder / "old.csv", self.folder / "new.json"
        first.write_bytes(b"original")

        def fail(stage):
            stage.write_bytes(b"partial")
            raise RuntimeError("writer failed")

        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            self.atomic({first: lambda p: p.write_bytes(b"replacement"), second: fail})
        self.assertEqual(first.read_bytes(), b"original")
        self.assertEqual(set(self.folder.iterdir()), {first})

    def test_partial_commit_failure_restores_existing_and_removes_new_file(self):
        first, second, third = [self.folder / name for name in ("old.csv", "new.json", "last.md")]
        first.write_bytes(b"original csv")
        third.write_bytes(b"original report")
        real_replace = os.replace

        def fail_last(source, target):
            if Path(target) == third and Path(source).name == third.name:
                raise PermissionError("locked destination")
            return real_replace(source, target)

        with patch("app.export.atomic.os.replace", side_effect=fail_last):
            with self.assertRaisesRegex(PermissionError, "locked destination"):
                self.atomic({p: lambda stage: stage.write_bytes(b"new") for p in (first, second, third)})
        self.assertEqual(first.read_bytes(), b"original csv")
        self.assertEqual(third.read_bytes(), b"original report")
        self.assertEqual(set(self.folder.iterdir()), {first, third})

    def test_missing_output_and_duplicate_paths_are_rejected(self):
        target = self.folder / "a.csv"
        target.write_bytes(b"original")
        with self.assertRaises(FileNotFoundError):
            self.atomic({target: lambda stage: None})
        with self.assertRaises(ValueError):
            self.atomic([(target, lambda stage: stage.write_bytes(b"new")),
                         (str(target), lambda stage: stage.write_bytes(b"other"))])
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(set(self.folder.iterdir()), {target})

    def test_backup_failure_never_publishes_staged_files(self):
        first, second = self.folder / "a.csv", self.folder / "b.json"
        first.write_bytes(b"original a")
        second.write_bytes(b"original b")
        with patch("app.export.atomic.shutil.copy2", side_effect=OSError("backup failed")):
            with self.assertRaisesRegex(OSError, "backup failed"):
                self.atomic({p: lambda stage: stage.write_bytes(b"new") for p in (first, second)})
        self.assertEqual(first.read_bytes(), b"original a")
        self.assertEqual(second.read_bytes(), b"original b")
        self.assertEqual(set(self.folder.iterdir()), {first, second})

    def test_rollback_failure_keeps_original_backup_for_recovery(self):
        first, second = self.folder / "a.csv", self.folder / "b.json"
        first.write_bytes(b"original a")
        second.write_bytes(b"original b")
        real_replace = os.replace

        def fail_commit_and_restore(source, target):
            if Path(target) == second or Path(source).name.endswith(".backup"):
                raise PermissionError("locked")
            return real_replace(source, target)

        with patch("app.export.atomic.os.replace", side_effect=fail_commit_and_restore):
            with self.assertRaises(exports.AtomicExportRollbackError) as caught:
                self.atomic({p: lambda stage: stage.write_bytes(b"new") for p in (first, second)})
        self.assertIsInstance(caught.exception.original_error, PermissionError)
        self.assertEqual(set(caught.exception.rollback_errors), {first})
        backup = caught.exception.backup_paths[first]
        self.assertEqual(backup.read_bytes(), b"original a")
        self.assertEqual(second.read_bytes(), b"original b")
        real_replace(backup, first)
        self.assertEqual(first.read_bytes(), b"original a")

    def test_empty_jobs_and_empty_output_are_supported(self):
        self.assertEqual(self.atomic([]), ())
        target = self.folder / "empty.csv"
        self.assertEqual(self.atomic([(target, lambda p: csv_export.export_samples([], p))]), (target,))
        self.assertEqual(target.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
