from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional


def build_report(
    batch_info: Dict,
    samples: List[Dict],
    events: List[Dict],
    manual_actions: List[Dict],
) -> str:
    lines = [
        "# Roast Report",
        "",
        f"Date: {batch_info.get('created_at', '-')}",
        f"Device: {batch_info.get('device', '-')}",
        f"Operator: {batch_info.get('operator', '-')}",
        f"Coffee: {batch_info.get('coffee_name', '-')}",
        "",
        f"Samples: {len(samples)}",
        f"Events: {len(events)}",
        f"Manual Actions: {len(manual_actions)}",
    ]
    sources = Counter(
        str(item.get("data_source") or item.get("source") or "UNKNOWN")
        for item in samples
    )
    lines.append("Data Sources: " + ", ".join(f"{key}={value}" for key, value in sorted(sources.items())))
    profiles = Counter(str(item.get("protocol_profile") or item.get("profile") or "-") for item in samples)
    lines.append("Protocol Profiles: " + ", ".join(f"{key}={value}" for key, value in sorted(profiles.items())))
    if samples:
        max_parse_errors = max(int(item.get("parse_error_count") or 0) for item in samples)
        lines.append(f"Max Parse Errors Before Sample: {max_parse_errors}")
    if samples:
        first = samples[0]
        last = samples[-1]
        lines.append(f"Duration: {float(last['time_s']) - float(first['time_s']):.1f}s")
        if first.get("bt") is not None and last.get("bt") is not None:
            lines.append(f"BT delta: {float(last['bt']) - float(first['bt']):.1f}°C")
    return "\n".join(lines)
