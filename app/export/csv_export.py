from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable

from app.roast.importer import restore_imported_sample


SAMPLE_FIELDS = (
    "timestamp", "time_ms", "time_s", "it", "et", "bt", "it_ror", "et_ror", "bt_ror",
    "source", "data_source", "source_port", "raw_frame", "parser_format", "temperature_unit", "channel_mapping",
    "source_baudrate", "protocol_profile", "time_basis", "parse_error_count",
    "work", "work_ror", "gap_before",
    "elapsed_time", "gas_mbar", "gas_kpa", "damper_level", "rpm_level",
    "control_scale", "device_time_s", "capture_time_s",
)


def export_samples(samples: Iterable[Dict], target: str | Path) -> None:
    target = Path(target)
    rows = list(samples)
    if not rows:
        target.write_text("", encoding="utf-8")
        return

    with target.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(SAMPLE_FIELDS)
        for row in rows:
            values = restore_imported_sample(row)
            for key, fallback in (("work", "CH4"), ("work_ror", "WORK_RoR")):
                if values.get(key) is None:
                    values[key] = row.get(fallback)
            writer.writerow([values.get(h) for h in SAMPLE_FIELDS])
