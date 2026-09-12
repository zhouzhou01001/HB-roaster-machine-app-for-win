from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from app.database.database import RoastDatabase
from app.ui.compare_window import CompareWindow
from app.ui.replay_window import ReplayWindow


def _samples(offset: float = 0.0) -> list[dict]:
    return [
        {"time_s": 0.0, "it": 160.0, "et": 180.0, "bt": 95.0 + offset, "bt_ror": 8.0},
        {"time_s": 1.0, "it": 165.0, "et": 185.0, "bt": 105.0 + offset, "bt_ror": 9.0},
    ]


def _events() -> list[dict]:
    return [
        {"event_type": "CHARGE", "time_s": 0.0, "bt": 95.0},
        {"event_type": "DROP", "time_s": 1.0, "bt": 105.0},
    ]


class ArchiveReadonlyViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = RoastDatabase(Path(self._temp_dir.name) / "readonly.hbroast")
        self.widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self.db.close()
        self._temp_dir.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def archive(self, number: int, offset: float = 0.0):
        return self.db.archive_session(
            _samples(offset),
            _events(),
            [{"time_s": 0.0, "gas_mbar": 18.0, "damper_level": 3, "rpm_level": 5}],
            {"batch_number": number, "coffee_name": f"archive-{number}"},
            charge_number=number,
        )

    def test_empty_database_produces_safe_read_only_empty_states(self) -> None:
        changes_before = self.db.conn.total_changes
        replay = self.keep(ReplayWindow.from_archive(self.db))
        compare = self.keep(CompareWindow(999, self.db, source="archive"))
        compare.mode_combo.setCurrentText("历史批次")

        self.assertEqual(replay.samples, [])
        self.assertFalse(replay.btn_play.isEnabled())
        self.assertIn("空数据", replay.source_status.text())
        self.assertFalse(compare.compare_btn.isEnabled())
        self.assertEqual(compare.target_combo.currentData(), ("empty", -1))
        self.assertEqual(self.db.conn.total_changes, changes_before)

    def test_single_archive_replays_but_has_no_second_batch_to_compare(self) -> None:
        archived = self.archive(1)
        changes_before = self.db.conn.total_changes

        replay = self.keep(ReplayWindow.from_archive(self.db, archived.batch_id))
        compare = self.keep(CompareWindow(archived.batch_id, self.db, source="archive"))
        compare.mode_combo.setCurrentText("历史批次")

        self.assertEqual(len(replay.samples), 2)
        self.assertEqual(len(replay.events), 2)
        self.assertEqual(len(replay.actions), 1)
        self.assertEqual(replay.source_kind, "archive")
        self.assertFalse(compare.compare_btn.isEnabled())
        self.assertEqual(self.db.conn.total_changes, changes_before)

    def test_two_archives_can_be_compared_without_mutating_database(self) -> None:
        first = self.archive(1)
        second = self.archive(2, offset=2.0)
        changes_before = self.db.conn.total_changes

        compare = self.keep(CompareWindow(second.batch_id, self.db, source="archive"))
        compare.mode_combo.setCurrentText("历史批次")
        self.assertEqual(compare.target_combo.count(), 1)
        self.assertEqual(compare.target_combo.currentData(), ("archive", first.batch_id))

        compare.compare_btn.click()
        self.app.processEvents()

        self.assertIn("豆温平均绝对偏差：2.00 C", compare.metrics_label.text())
        self.assertEqual(self.db.conn.total_changes, changes_before)

    def test_legacy_database_rows_remain_readable(self) -> None:
        roast_id = self.db.create_batch(coffee_name="legacy")
        for sample in _samples():
            self.db.insert_sample(sample, roast_id=roast_id)
        for event in _events():
            self.db.insert_event(
                roast_id,
                float(event["time_s"]),
                str(event["event_type"]),
                bt=float(event["bt"]),
            )
        self.db.insert_manual_action(roast_id, 0.0, 1.8, 3.0, 5.0)
        changes_before = self.db.conn.total_changes

        replay = self.keep(ReplayWindow.from_database(self.db, roast_id, source="legacy"))
        compare = self.keep(CompareWindow(roast_id, self.db, source="legacy"))

        self.assertEqual(replay.source_kind, "legacy")
        self.assertEqual(len(replay.samples), 2)
        self.assertEqual(len(compare.current_samples), 2)
        self.assertEqual(self.db.conn.total_changes, changes_before)

    def test_secondary_windows_share_read_only_hierarchy_and_responsive_controls(self) -> None:
        archived = self.archive(1)
        replay = self.keep(ReplayWindow.from_archive(self.db, archived.batch_id))
        compare = self.keep(CompareWindow(archived.batch_id, self.db, source="archive"))

        self.assertEqual(
            tuple(replay.speed.itemText(index) for index in range(replay.speed.count())),
            ("0.5x", "1x", "2x"),
        )
        self.assertEqual(replay.source_status.property("uiRole"), "statusBadge")
        self.assertEqual(compare.status_badge.property("uiRole"), "statusBadge")
        for window in (replay, compare):
            self.assertFalse(
                any(label.text().startswith("原：") for label in window.findChildren(QtWidgets.QLabel))
            )
            window.resize(700, 600)
            window.show()
        self.app.processEvents()

        self.assertEqual(replay.property("layoutMode"), "compact")
        self.assertEqual(compare.property("layoutMode"), "compact")
        self.assertEqual(
            tuple(compare.mode_combo.itemText(index) for index in range(compare.mode_combo.count())),
            ("参考曲线", "历史批次"),
        )


if __name__ == "__main__":
    unittest.main()
