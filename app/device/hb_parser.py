from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, Optional


CHANNEL_KEYS = ("CH1", "CH2", "CH3", "CH4")
TEMPERATURE_UNIT = "C"
SUPPORTED_PROFILES = ("HB_MODEL_S", "HB_TC4", "HB_LEGACY_3")


class HbParser:
    """
    Parse one-line raw payload from HB serial stream.
    Supports:
    - JSON payload
    - KEY:VALUE pairs
    - Plain numeric tuples ("1,2,3")

    All temperature values are interpreted as degrees Celsius. Supported
    numeric3 frames are ``CH1,CH2,CH3``; numeric4 frames are
    ``time_s,CH1,CH2,CH3``. No binary byte order or checksum is assumed.
    """

    def __init__(self) -> None:
        self.last_error = ""
        self.last_format = ""

    def parse(
        self,
        raw: str,
        when: Optional[float] = None,
        profile: str = "HB_LEGACY_3",
    ) -> Optional[Dict[str, Any]]:
        self.last_error = ""
        self.last_format = ""
        profile = str(profile or "").strip().upper()
        if profile not in SUPPORTED_PROFILES:
            self.last_error = f"未知协议 profile：{profile or '-'}"
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if raw is None:
            self.last_error = "空帧"
            return None
        text = str(raw).strip()
        if not text:
            self.last_error = "空帧"
            return None

        upper = text.upper()
        if upper.startswith("READ") or upper.startswith("CHAN;"):
            self.last_format = "command_echo"
            self.last_error = "设备命令回显"
            return None

        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                normalized = self._normalize_dict(payload, timestamp=when, format_name="json")
                if normalized is None:
                    self.last_error = "JSON 帧没有受支持的温度通道"
                return normalized
        except Exception:
            pass

        unit_match = re.search(r"(?:UNIT|TEMP_UNIT|TEMPERATURE_UNIT)\s*[:=]\s*([A-Za-z°]+)", text, flags=re.IGNORECASE)
        if unit_match and unit_match.group(1).strip().upper() not in {"C", "°C", "CELSIUS"}:
            self.last_error = f"不支持的温度单位：{unit_match.group(1).strip()}"
            return None

        # Common format: "IT:165.2,ET:180.9,BT:112.4"
        pair_matches = re.findall(r"([A-Za-z0-9_]+)\s*[:=]\s*([-+]?\d*\.?\d+)", text)
        if pair_matches:
            data: Dict[str, float] = {}
            for key, value in pair_matches:
                try:
                    data[key.upper()] = float(value)
                except ValueError:
                    continue
            normalized = self._normalize_dict(data, timestamp=when, format_name="key_value")
            if normalized is None:
                self.last_error = "键值帧没有受支持的温度通道"
            return normalized

        # Common format: "165.2 180.9 112.4"
        # Common formats: "165.2,180.9,112.4" or prefixed by timestamp
        values = [p.strip() for p in re.split(r"[;,\s]+", text) if p.strip()]
        if profile == "HB_TC4" and len(values) >= 5 and all(_is_number(value) for value in values[:5]):
            self.last_format = "numeric5"
            return {
                "time_s": float(values[0]),
                "CH1": float(values[1]),
                "CH2": float(values[2]),
                "CH3": float(values[3]),
                "CH4": float(values[4]),
                "parser_format": "numeric5",
                "temperature_unit": TEMPERATURE_UNIT,
                "time_basis": "device_time_s",
            }
        if profile == "HB_TC4" and len(values) >= 4 and all(_is_number(value) for value in values[:4]):
            self.last_format = "numeric4_tc4"
            return {
                "CH1": float(values[0]),
                "CH2": float(values[1]),
                "CH3": float(values[2]),
                "CH4": float(values[3]),
                "time_s": when if when is not None else time.time(),
                "parser_format": "numeric4_tc4",
                "temperature_unit": TEMPERATURE_UNIT,
                "time_basis": "capture_relative",
            }
        if profile == "HB_LEGACY_3" and len(values) >= 4 and all(_is_number(value) for value in values[:4]):
            self.last_format = "numeric4"
            return {
                "time_s": float(values[0]),
                "CH1": float(values[1]),
                "CH2": float(values[2]),
                "CH3": float(values[3]),
                "parser_format": "numeric4",
                "temperature_unit": TEMPERATURE_UNIT,
                "time_basis": "device_time_s",
            }
        if profile == "HB_LEGACY_3" and len(values) >= 3 and all(_is_number(v) for v in values[:3]):
            self.last_format = "numeric3"
            return {
                "CH1": float(values[0]),
                "CH2": float(values[1]),
                "CH3": float(values[2]),
                "time_s": when if when is not None else time.time(),
                "parser_format": "numeric3",
                "temperature_unit": TEMPERATURE_UNIT,
                "time_basis": "capture_relative",
            }

        if profile == "HB_TC4" and len(values) >= 3 and all(_is_number(v) for v in values[:3]):
            self.last_error = "HB_TC4 帧缺少第四路 CH4，拒绝三值帧"
            return None
        self.last_error = "不支持的帧格式或字段不足"
        return None

    def _normalize_dict(
        self,
        data: Dict,
        timestamp: Optional[float] = None,
        format_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        declared_unit = None
        has_device_time = False
        for key, value in data.items():
            up = str(key).strip().upper()
            if up in {"UNIT", "TEMP_UNIT", "TEMPERATURE_UNIT"}:
                declared_unit = str(value).strip().upper()
                continue
            if up in {"IT", "IT_RAW", "IT_RAW1"}:
                try:
                    payload["IT_RAW"] = float(value)
                except (TypeError, ValueError):
                    continue
            elif up in {"ET", "ET_RAW", "ET_RAW2"}:
                try:
                    payload["ET_RAW"] = float(value)
                except (TypeError, ValueError):
                    continue
            elif up in {"BT", "BT_RAW", "BT_RAW3"}:
                try:
                    payload["BT_RAW"] = float(value)
                except (TypeError, ValueError):
                    continue
            elif up in CHANNEL_KEYS:
                try:
                    payload[up] = float(value)
                except Exception:
                    continue
            elif up in {"TIME_S", "TIME", "T", "TIMESTAMP"}:
                has_device_time = True
                try:
                    payload["time_s"] = float(value)
                except Exception:
                    pass
            elif up in {"T1", "T2", "T3", "T4"}:
                index = {"T1": "CH1", "T2": "CH2", "T3": "CH3", "T4": "CH4"}[up]
                try:
                    payload[index] = float(value)
                except (TypeError, ValueError):
                    continue

        if "time_s" not in payload:
            payload["time_s"] = timestamp if timestamp is not None else time.time()
        if declared_unit and declared_unit not in {"C", "°C", "CELSIUS"}:
            self.last_error = f"不支持的温度单位：{declared_unit}"
            return None
        if not any(key in payload for key in ("IT_RAW", "ET_RAW", "BT_RAW", "CH1", "CH2", "CH3")):
            return None
        payload["parser_format"] = format_name or "mapping"
        payload["temperature_unit"] = TEMPERATURE_UNIT
        payload["time_basis"] = "device_time_s" if has_device_time else "capture_relative"
        self.last_format = payload["parser_format"]

        return payload


def _is_number(value: str) -> bool:
    try:
        float(value)
        return math.isfinite(float(value))
    except Exception:
        return False
