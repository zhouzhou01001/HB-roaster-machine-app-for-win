from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from app.utils.paths import default_channel_mapping_path

MAPPING_ORDER = ("IT", "ET", "BT")
OPTIONAL_MAPPING_TARGETS = ("WORK", "AMBIENT")


@dataclass
class ChannelMapper:
    config_path: Path = field(default_factory=default_channel_mapping_path)
    mapping: Dict[str, str] = field(default_factory=dict)
    last_error: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.config_path = Path(self.config_path)
        self.load()
        if not self.mapping:
            self.set_mapping({"CH1": "ET", "CH2": "IT", "CH3": "BT", "CH4": "WORK"})

    def load(self) -> None:
        if self.config_path.exists():
            try:
                self.mapping = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                self.mapping = {}
        if not isinstance(self.mapping, dict):
            self.mapping = {}
        try:
            self.mapping = self._normalize_mapping(self.mapping)
        except ValueError as exc:
            self.last_error = str(exc)

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_mapping(self, mapping: Dict[str, str]) -> None:
        norm = self._normalize_mapping(mapping)
        self.mapping = norm
        self.save()

    @staticmethod
    def _normalize_mapping(mapping: Dict[str, str]) -> Dict[str, str]:
        if not isinstance(mapping, dict):
            raise ValueError("温度通道映射必须为字典")
        norm = {}
        for source, target in mapping.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError("温度通道映射必须使用字符串")
            source, target = source.strip().upper(), target.strip().upper()
            if not source or target not in (*MAPPING_ORDER, *OPTIONAL_MAPPING_TARGETS):
                raise ValueError("温度通道映射包含无效通道")
            if source in norm or target in norm.values():
                raise ValueError("温度通道映射必须一一对应，不能重复")
            norm[source] = target
        # Extend old three-channel files in memory, preserving every physical assignment.
        if set(norm) == {"CH1", "CH2", "CH3"} and set(norm.values()) == set(MAPPING_ORDER):
            norm["CH4"] = "WORK"
        return norm

    @property
    def is_complete(self) -> bool:
        try:
            mapping = self._normalize_mapping(self.mapping)
        except ValueError:
            return False
        return all(k in mapping.values() for k in MAPPING_ORDER)

    @property
    def mapping_text(self) -> str:
        return ",".join(f"{source}->{target.upper()}" for source, target in sorted(self.mapping.items()))

    def map_sample(self, sample: Dict[str, float], fallback_time: Optional[float] = None) -> Optional[Dict[str, float]]:
        self.last_error = ""
        if not sample:
            self.last_error = "空采样帧"
            return None
        raw_alias = {
            "IT_RAW": "it",
            "ET_RAW": "et",
            "BT_RAW": "bt",
            "T1": "it",
            "T2": "et",
            "T3": "bt",
        }
        normalized = dict(sample)
        direct_values: Dict[str, list[float]] = {"it": [], "et": [], "bt": []}
        for alias, target in raw_alias.items():
            if alias in normalized and normalized[alias] is not None:
                value = self._as_optional(normalized[alias])
                if value is None:
                    self.last_error = f"{alias} 温度不是有限数值"
                    return None
                direct_values[target].append(value)
        for target in direct_values:
            if len(direct_values[target]) > 1 and max(direct_values[target]) - min(direct_values[target]) > 0.01:
                self.last_error = f"{target.upper()} 别名通道冲突"
                return None
        for target in ("it", "et", "bt"):
            if target in normalized and normalized[target] is not None:
                value = self._as_optional(normalized[target])
                if value is None:
                    self.last_error = f"{target.upper()} 温度不是有限数值"
                    return None
                direct_values[target].append(value)

        if not self.is_complete:
            self.last_error = "温度通道映射不完整"
            return None

        time_value = sample.get("time_s", fallback_time)
        if time_value is None:
            self.last_error = "采样缺少时间戳"
            return None
        time_s = self._as_optional(time_value)
        if time_s is None:
            self.last_error = "采样时间不是有限数值"
            return None
        out = {"time_s": time_s}
        inv = {v: k for k, v in self._normalize_mapping(self.mapping).items()}
        for target in MAPPING_ORDER:
            source = inv.get(target)
            mapped_value = self._as_optional(sample.get(source)) if source and source in sample else None
            direct_value = direct_values[target.lower()][0] if direct_values[target.lower()] else None
            if direct_value is not None and mapped_value is not None and abs(direct_value - mapped_value) > 0.01:
                self.last_error = f"{target} 与 {source} 通道值冲突"
                return None
            value = direct_value if direct_value is not None else mapped_value
            if value is None:
                self.last_error = f"帧缺少完整温度通道：{target}"
                return None
            out[target.lower()] = value
        for target in OPTIONAL_MAPPING_TARGETS:
            source = inv.get(target)
            if source and source in sample:
                value = self._as_optional(sample.get(source))
                if value is not None:
                    out[target.lower()] = value
        return out

    @staticmethod
    def _as_optional(value):
        try:
            if value is None:
                return None
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        except Exception:
            return None
