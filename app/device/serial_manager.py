from __future__ import annotations

import threading
import time
import math
from typing import Optional

from PySide6 import QtCore

from .hb_parser import HbParser, SUPPORTED_PROFILES

try:
    import serial
    import serial.tools.list_ports
except Exception:  # pragma: no cover
    serial = None


class SerialManager(QtCore.QObject):
    """
    Manage serial port and emit parsed samples.
    For development we can run simulation mode to provide synthetic samples.
    """

    raw_received = QtCore.Signal(str)
    sample_received = QtCore.Signal(dict)
    state_changed = QtCore.Signal(bool)
    command_sent = QtCore.Signal(str)
    error = QtCore.Signal(str)
    parse_error = QtCore.Signal(str)
    parse_error_detail = QtCore.Signal(str, str, int)

    SUPPORTED_BAUDRATES = (9600, 19200, 115200)

    def __init__(self, parser: Optional[HbParser] = None, parent=None) -> None:
        super().__init__(parent)
        self.parser = parser or HbParser()
        self._serial = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._timer: Optional[QtCore.QTimer] = None
        self._simulate = False
        self._state_lock = threading.Lock()
        self._connected_state = False
        self._source_port = ""
        self._requested_baudrate = 0
        self._actual_baudrate = 0
        self._profile = ""
        self.parse_error_count = 0
        self.valid_frame_count = 0
        self.last_raw_frame = ""
        self.last_parse_error = ""
        self._base_time = time.time()
        self._it = 120.0
        self._et = 125.0
        self._bt = 90.0
        self._work = 130.0
        self._last_valid_frame_at = time.monotonic()
        self._feedback_timeout_s = 5.0

    def connect(
        self,
        port: Optional[str],
        baudrate: int = 115200,
        profile: str = "HB_TC4",
    ) -> None:
        if self._running:
            return
        normalized_port = str(port).strip() if port is not None else ""
        if not normalized_port:
            self._simulate = False
            self.error.emit("未选择采集源：请选择真实 COM 端口或显式选择 SIMULATION。")
            return
        if normalized_port.upper() == "SIMULATION":
            self._simulate = True
            self._source_port = "SIMULATION"
            self._profile = "SIMULATION"
            self._requested_baudrate = 0
            self._actual_baudrate = 0
            self.start_simulation()
            return
        normalized_profile = str(profile or "").strip().upper()
        if normalized_profile not in SUPPORTED_PROFILES:
            self._simulate = False
            self.error.emit(f"未知协议 profile：{normalized_profile or '-'}，已拒绝连接。")
            return
        try:
            requested_baudrate = int(baudrate)
        except (TypeError, ValueError):
            self.error.emit("波特率无效：请选择 9600、19200 或 115200。")
            return
        if requested_baudrate not in self.SUPPORTED_BAUDRATES:
            self.error.emit(f"不支持的波特率：{requested_baudrate}。")
            return
        if serial is None:
            self._simulate = False
            self.error.emit("pyserial is not available; serial monitoring cannot start.")
            return
        try:
            self._simulate = False
            self.parse_error_count = 0
            self.valid_frame_count = 0
            self.last_raw_frame = ""
            self.last_parse_error = ""
            self._source_port = normalized_port
            self._profile = normalized_profile
            self._requested_baudrate = requested_baudrate
            self._serial = serial.serial_for_url(normalized_port, baudrate=requested_baudrate, timeout=0.4)
            self._actual_baudrate = int(getattr(self._serial, "baudrate", requested_baudrate))
            self._base_time = time.time()
            self._running = True
            self._last_valid_frame_at = time.monotonic()
            with self._state_lock:
                self._connected_state = True
            self.state_changed.emit(True)
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
        except Exception as exc:
            self._serial = None
            self._running = False
            self._simulate = False
            self._actual_baudrate = 0
            detail = str(exc)
            lowered = detail.lower()
            if any(token in lowered for token in ("access is denied", "permission", "拒绝访问")):
                reason = f"串口被占用或无访问权限：{normalized_port}"
            elif any(token in lowered for token in ("cannot find", "not found", "no such file", "找不到")):
                reason = f"串口未找到：{normalized_port}"
            elif "timeout" in lowered or "超时" in lowered:
                reason = f"串口连接超时：{normalized_port}"
            else:
                reason = f"串口连接失败：{normalized_port}；{detail}"
            self.error.emit(reason)

    def reset_elapsed_origin(self) -> None:
        """Reset device-relative elapsed time without restarting the reader thread."""
        self._base_time = time.time()

    def disconnect(self) -> None:
        self._running = False
        thread = self._thread
        self._thread = None
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        with self._state_lock:
            should_emit = self._connected_state
            self._connected_state = False
        if should_emit:
            self.state_changed.emit(False)

    def start_simulation(self, interval_ms: int = 800) -> None:
        self.disconnect()
        self._simulate = True
        self.parse_error_count = 0
        self.valid_frame_count = 0
        self.last_raw_frame = ""
        self.last_parse_error = ""
        self._source_port = "SIMULATION"
        self._base_time = time.time()
        self._running = True
        with self._state_lock:
            self._connected_state = True
        self.state_changed.emit(True)
        timer = QtCore.QTimer(self)
        timer.setInterval(interval_ms)
        timer.timeout.connect(self._emit_sim_sample)
        timer.start()
        self._timer = timer

    @staticmethod
    def available_ports() -> list[str]:
        return [item["device"] for item in SerialManager.available_port_details()]

    @staticmethod
    def available_port_details() -> list[dict]:
        if serial is None:
            return []
        details = []
        for port in serial.tools.list_ports.comports():
            description = (port.description or "未知设备").strip()
            manufacturer = (port.manufacturer or "").strip()
            text = f"{description} {manufacturer}".upper()
            if any(token in text for token in ("CH340", "CH341", "CP210", "SILICON LABS", "USB-SERIAL")):
                usb_family = "CH340/CH341/CP210x"
            elif any(token in text for token in ("CH43", "CH34", "CH43X", "CH34X")):
                usb_family = "CH43x（待验证）"
            else:
                usb_family = "未确认 USB 芯片"
            vid_pid = ""
            if port.vid is not None or port.pid is not None:
                vid_pid = f"VID:PID {port.vid or 0:04X}:{port.pid or 0:04X}"
            display = " | ".join(item for item in (port.device, description, manufacturer, usb_family, vid_pid) if item)
            details.append(
                {
                    "device": port.device,
                    "description": description,
                    "manufacturer": manufacturer,
                    "hwid": (port.hwid or "").strip(),
                    "serial_number": (port.serial_number or "").strip() if hasattr(port, "serial_number") else "",
                    "vid": port.vid,
                    "pid": port.pid,
                    "usb_family": usb_family,
                    "display": display,
                }
            )
        return details

    def _read_loop(self) -> None:
        while self._running and self._serial is not None:
            try:
                if self._profile == "HB_MODEL_S":
                    before_count = self.valid_frame_count
                    self._read_model_s_sample()
                    if self.valid_frame_count == before_count and time.monotonic() - self._last_valid_frame_at >= self._feedback_timeout_s:
                        self.error.emit("通信超时：设备无温度反馈，请检查烘焙机电源、USB 线和端口配置。")
                        self.disconnect()
                        break
                    continue
                self._send_serial_command("READ")
                raw_line = self._serial.readline()
                if raw_line and not raw_line.endswith((b"\n", b"\r")):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    self.raw_received.emit(line)
                    self._record_parse_error(line, "帧未以换行结束，可能是半帧")
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    if time.monotonic() - self._last_valid_frame_at >= self._feedback_timeout_s:
                        self.error.emit("通信超时：设备无温度反馈，请检查烘焙机电源、USB 线和端口配置。")
                        self.disconnect()
                        break
                    continue
                self.raw_received.emit(line)
                parsed = self.parser.parse(
                    line,
                    when=time.time() - self._base_time,
                    profile=self._profile,
                )
                if parsed:
                    if self._profile == "HB_TC4" and not all(key in parsed for key in ("CH1", "CH2", "CH3", "CH4")):
                        self._record_parse_error(line, "TC4 帧缺少 CH1-CH4 完整四路数据")
                        continue
                    self.valid_frame_count += 1
                    self._last_valid_frame_at = time.monotonic()
                    parsed.update(
                        {
                            "timestamp": time.time(),
                            "source": "SERIAL",
                            "data_source": "SERIAL",
                            "port": self._source_port,
                            "source_port": self._source_port,
                            "raw_frame": line,
                            "parser_format": parsed.get("parser_format") or self.parser.last_format,
                            "temperature_unit": parsed.get("temperature_unit") or "C",
                            "baudrate": self._actual_baudrate,
                            "profile": self._profile,
                            "parse_error_count": self.parse_error_count,
                            "time_basis": parsed.get("time_basis") or "capture_relative",
                        }
                    )
                    self.sample_received.emit(parsed)
                else:
                    self._record_parse_error(line)
            except Exception as exc:
                self.error.emit(f"Serial read error: {exc}")
                self.disconnect()
                break

    def _read_model_s_sample(self) -> None:
        """Read all four HB Model S temperature channels through TC4 pairs."""
        first = self._request_tc4_pair("1200")
        if first is None:
            return
        second = self._request_tc4_pair("3400")
        if first is None or second is None:
            return

        # Machine channel order confirmed from the connected roaster:
        # CHAN;1200 returns exhaust then inlet; CHAN;3400 returns BT then ET.
        data = {
            "timestamp": time.time(),
            "time_s": time.time() - self._base_time,
            "AT_RAW": first[0],
            "IT_RAW": first[2],
            "CH4": first[1],
            "BT_RAW": second[1],
            "ET_RAW": second[2],
            "source": "SERIAL",
            "data_source": "SERIAL",
            "port": self._source_port,
            "source_port": self._source_port,
            "raw_frame": f"CHAN;1200=>{first[3]} | CHAN;3400=>{second[3]}",
            "parser_format": "hb_model_s_tc4_pairs",
            "temperature_unit": "C",
            "baudrate": self._actual_baudrate,
            "profile": self._profile,
            "parse_error_count": self.parse_error_count,
            "time_basis": "capture_relative",
        }
        self.valid_frame_count += 1
        self._last_valid_frame_at = time.monotonic()
        self.sample_received.emit(data)

    def _request_tc4_pair(self, channel_config: str):
        if self._serial is None:
            return None
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._send_serial_command(f"CHAN;{channel_config}")
        time.sleep(0.1)
        acknowledgement = self._serial.readline()
        ack_text = acknowledgement.decode("utf-8", errors="replace").strip()
        if ack_text:
            self.raw_received.emit(ack_text)
        # Verified Model S firmware can select channels without replying.
        # Silence is not an error; an explicit malformed reply still is.
        if acknowledgement and (not ack_text.startswith("#") or not acknowledgement.endswith((b"\n", b"\r"))):
            self._record_parse_error(ack_text, "HB Model S CHAN 命令未返回完整确认")
            return None
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._send_serial_command("READ")
        time.sleep(0.1)
        raw_line = self._serial.readline()
        if not raw_line:
            self._record_parse_error("", f"HB Model S CHAN;{channel_config} 后 READ 无返回")
            return None
        line = raw_line.decode("utf-8", errors="replace").strip()
        self.raw_received.emit(line)
        if not raw_line.endswith((b"\n", b"\r")):
            self._record_parse_error(line, "HB Model S READ 返回半帧")
            return None
        values = [part.strip() for part in line.replace(";", ",").split(",") if part.strip()]
        if len(values) < 3:
            self._record_parse_error(line, "HB Model S READ 返回字段不足，至少需要三个数值")
            return None
        try:
            numeric = [float(value) for value in values[:3]]
        except ValueError:
            self._record_parse_error(line, "HB Model S READ 返回不是数字帧")
            return None
        if not all(math.isfinite(value) for value in numeric):
            self._record_parse_error(line, "HB Model S READ 返回非有限数值")
            return None
        return numeric[0], numeric[1], numeric[2], line

    def _send_serial_command(self, command: str) -> None:
        if self._serial is None:
            return
        if not command:
            return
        line_ending = "\n" if self._profile == "HB_MODEL_S" else "\r\n"
        line = f"{command}{line_ending}"
        try:
            self._serial.write(line.encode("utf-8"))
            self.command_sent.emit(command)
        except Exception as exc:
            self.error.emit(f"Serial command send failed: {exc}")

    def _record_parse_error(self, raw_frame: str, reason: Optional[str] = None) -> None:
        self.parse_error_count += 1
        self.last_raw_frame = raw_frame
        reason = reason or self.parser.last_error or "无法识别的串口帧"
        self.last_parse_error = reason
        parsed_reason = f"串口帧异常 #{self.parse_error_count}：{reason}；原始帧：{raw_frame[:120]}"
        self.parse_error.emit(parsed_reason)
        self.parse_error_detail.emit(reason, raw_frame, self.parse_error_count)

    def _emit_sim_sample(self) -> None:
        if not self._running:
            return
        self._it += 0.3
        self._et += 0.45 + (0.02 if self._et < 180 else -0.2)
        self._bt += 0.5 + (0.05 if self._it < 170 else -0.1)
        self._work += 0.2
        data = {
            "timestamp": time.time(),
            "time_s": time.time() - self._base_time,
            "CH1": round(self._it, 2),
            "CH2": round(self._et, 2),
            "CH3": round(self._bt, 2),
            "CH4": round(self._work, 2),
            "source": "SIMULATION",
            "data_source": "SIMULATION",
            "port": "SIMULATION",
            "source_port": "SIMULATION",
            "parser_format": "simulation",
            "temperature_unit": "C",
            "baudrate": 0,
            "profile": "SIMULATION",
            "parse_error_count": self.parse_error_count,
            "time_basis": "simulation_elapsed",
        }
        data["raw_frame"] = f"{data['time_s']:.2f}, {data['CH1']}, {data['CH2']}, {data['CH3']}, {data['CH4']}"
        self.raw_received.emit(data["raw_frame"])
        self.sample_received.emit(data)
