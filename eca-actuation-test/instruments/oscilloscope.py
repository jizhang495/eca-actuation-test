"""Generic SCPI oscilloscope driver for voltage measurements."""

import logging
import math
import os
import re
import select
import time
from pathlib import Path
from typing import Optional

import pyvisa

logger = logging.getLogger(__name__)


class Oscilloscope:
    """Generic SCPI oscilloscope driver.

    The driver reads channel mean/DC voltage with a small set of common SCPI
    command variants used by Tektronix, Keysight/Agilent, Rigol, and similar
    scopes. Unsupported commands are ignored and the next query variant is tried.
    """

    _FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
    _USBTMC_PREFIX = "USBTMC::"
    _USBTMC_QUERY_TIMEOUT_SECONDS = 1.0
    _TEKTRONIX_INVALID_THRESHOLD = 1e30
    _WAVEFORM_SAMPLE_POINTS = 20

    def __init__(self, visa_address: Optional[str] = None):
        self.visa_address = visa_address
        self.instrument: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        self._is_connected = False
        self._usbtmc_fd: Optional[int] = None
        self._idn: Optional[str] = None
        self._last_acquisition_start: Optional[float] = None
        self.last_error: Optional[str] = None

    def list_available_devices(self) -> list[str]:
        """List all available USB VISA devices."""
        resources: list[str] = []
        try:
            resources.extend(str(res) for res in self.rm.list_resources() if str(res).upper().startswith("USB"))
        except Exception as e:
            logger.error(f"Failed to list VISA resources: {e}")

        for device in self.discover_usbtmc_devices():
            resource = str(device["resource"])
            if resource not in resources:
                resources.append(resource)

        return resources

    @classmethod
    def discover_usbtmc_devices(cls) -> list[dict]:
        """Discover Linux USBTMC character devices from sysfs."""
        devices: list[dict] = []

        for sysfs_entry in sorted(Path("/sys/class/usbmisc").glob("usbtmc*")):
            device_name = sysfs_entry.name
            device_path = f"/dev/{device_name}"
            interface_path = (sysfs_entry / "device").resolve()
            usb_device_path = interface_path.parent

            def read_attr(name: str) -> Optional[str]:
                try:
                    return (usb_device_path / name).read_text().strip()
                except OSError:
                    return None

            vendor_id = read_attr("idVendor")
            product_id = read_attr("idProduct")
            manufacturer = read_attr("manufacturer")
            product = read_attr("product")
            serial = read_attr("serial")
            name_parts = [part for part in (manufacturer, product, serial) if part]

            devices.append(
                {
                    "resource": cls.format_usbtmc_resource(device_path),
                    "device_path": device_path,
                    "idn": ", ".join(name_parts) if name_parts else device_path,
                    "manufacturer": manufacturer,
                    "product": product,
                    "serial": serial,
                    "vendor_id": vendor_id,
                    "product_id": product_id,
                }
            )

        return devices

    @classmethod
    def format_usbtmc_resource(cls, device_path: str) -> str:
        return f"{cls._USBTMC_PREFIX}{device_path}::INSTR"

    def connect(self, visa_address: Optional[str] = None) -> bool:
        """Connect to the oscilloscope."""
        if visa_address:
            self.visa_address = visa_address

        if not self.visa_address:
            logger.error("No oscilloscope VISA address specified")
            return False

        try:
            self.last_error = None
            if self._is_usbtmc_resource(self.visa_address):
                device_path = self._usbtmc_device_path(self.visa_address)
                self._usbtmc_fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
                idn = self._query("*IDN?").strip()
            else:
                self.instrument = self.rm.open_resource(self.visa_address)
                self.instrument.timeout = 5000
                idn = self._query("*IDN?").strip()

            logger.info(f"Connected to oscilloscope: {idn}")
            self._idn = idn
            self._is_connected = True
            self.configure_voltage_channels()
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to connect to oscilloscope at {self.visa_address}: {e}")
            self._is_connected = False
            if self._usbtmc_fd is not None:
                os.close(self._usbtmc_fd)
                self._usbtmc_fd = None
            self.instrument = None
            return False

    def disconnect(self):
        """Disconnect from the oscilloscope."""
        try:
            if self._usbtmc_fd is not None:
                os.close(self._usbtmc_fd)
                logger.info("Oscilloscope USBTMC device closed")
            if self.instrument:
                self.instrument.close()
                logger.info("Oscilloscope disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting oscilloscope: {e}")
        finally:
            self._is_connected = False
            self._usbtmc_fd = None
            self._idn = None
            self._last_acquisition_start = None
            self.instrument = None

    def configure_voltage_channels(self):
        """Best-effort configuration for channel 1 and 2 voltage measurements."""
        if not self._is_connected or (not self.instrument and self._usbtmc_fd is None):
            logger.warning("Oscilloscope not connected")
            return

        for channel in (1, 2):
            for command in (
                f":CHAN{channel}:DISP ON",
                f":CHAN{channel}:COUP DC",
                f":CHANnel{channel}:DISPlay ON",
                f":CHANnel{channel}:COUPling DC",
            ):
                try:
                    self._write(command)
                except Exception:
                    logger.debug("Oscilloscope config command not accepted: %s", command)

    def start_acquisition(self):
        """Put the oscilloscope into continuous acquisition/run mode."""
        if not self._is_connected or (not self.instrument and self._usbtmc_fd is None):
            logger.warning("Oscilloscope not connected")
            return

        for command in (
            "ACQuire:STATE ON",
            "ACQ:STATE ON",
            "ACQuire:STOPAfter RUNSTop",
        ):
            try:
                self._write(command)
            except Exception:
                logger.debug("Oscilloscope acquisition command not accepted: %s", command)

        self._last_acquisition_start = time.monotonic()

    def stop_acquisition(self):
        """Stop acquisition so the current waveform record can be exported."""
        if not self._is_connected or (not self.instrument and self._usbtmc_fd is None):
            logger.warning("Oscilloscope not connected")
            return

        for command in ("ACQuire:STATE OFF", "ACQ:STATE OFF"):
            try:
                self._write(command)
                break
            except Exception:
                logger.debug("Oscilloscope acquisition stop command not accepted: %s", command)

        self._last_acquisition_start = None

    def read_voltages(self) -> tuple[Optional[float], Optional[float]]:
        """Read channel 1 and channel 2 voltages."""
        if self._is_tektronix_scope:
            return (
                self._read_tektronix_waveform_voltage(1, latest_sample=True),
                self._read_tektronix_waveform_voltage(2, latest_sample=True),
            )

        return (self.read_channel_voltage(1), self.read_channel_voltage(2))

    def read_channel_voltage(self, channel: int) -> Optional[float]:
        """Read the mean/DC voltage for a single oscilloscope channel."""
        if channel not in (1, 2):
            raise ValueError("Only oscilloscope channels 1 and 2 are supported")

        if not self._is_connected or (not self.instrument and self._usbtmc_fd is None):
            logger.warning("Oscilloscope not connected")
            return None

        self.start_acquisition()

        if self._is_tektronix_scope:
            # TBS 2000B scopes report measurement values as unavailable in
            # untriggered Roll mode. Read waveform data instead so slow DC
            # transients and low-frequency signals still stream correctly.
            waveform_value = self._read_tektronix_waveform_voltage(channel, latest_sample=True)
            if waveform_value is not None:
                return waveform_value

            tektronix_value = self._read_tektronix_immediate_measurement(channel)
            if tektronix_value is not None:
                return tektronix_value

        query_variants = (
            f":MEASure:VAVerage? CHANnel{channel}",
            f":MEASure:VAVerage? CHAN{channel}",
            f":MEASure:MEAN? CHANnel{channel}",
            f":MEASure:MEAN? CHAN{channel}",
            f":MEASure:VDC? CHANnel{channel}",
            f":MEASure:VDC? CHAN{channel}",
            f":MEASure:ITEM? VAVG,CHANnel{channel}",
            f":MEASure:ITEM? VAVG,CHAN{channel}",
            f"C{channel}:PAVA? MEAN",
            f"C{channel}:PAVA? VAVG",
        )

        for query in query_variants:
            value = self._query_float(query)
            if value is not None:
                return value

        waveform_value = self._read_tektronix_waveform_voltage(channel, latest_sample=True)
        if waveform_value is not None:
            return waveform_value

        if not self._is_tektronix_scope:
            tektronix_value = self._read_tektronix_immediate_measurement(channel)
            if tektronix_value is not None:
                return tektronix_value

        logger.error("Failed to read oscilloscope channel %s voltage", channel)
        return None

    def _read_tektronix_immediate_measurement(self, channel: int) -> Optional[float]:
        """Read voltage using Tektronix immediate measurement commands."""
        if not self.instrument and self._usbtmc_fd is None:
            return None

        command_sets = (
            (
                f"MEASUrement:IMMed:SOUrce1 CH{channel}",
                "MEASUrement:IMMed:TYPe MEAN",
                "MEASUrement:IMMed:VALue?",
            ),
            (
                f"MEASU:IMM:SOU1 CH{channel}",
                "MEASU:IMM:TYP MEAN",
                "MEASU:IMM:VAL?",
            ),
        )

        for source_command, type_command, value_query in command_sets:
            try:
                self._write(source_command)
                self._write(type_command)
            except Exception:
                continue

            value = self._query_float(value_query)
            if value is not None:
                return value

        return None

    def capture_waveforms(self, stop_elapsed_seconds: Optional[float] = None) -> Optional[dict]:
        """Stop acquisition and export CH1/CH2 waveform records."""
        if not self._is_connected or (not self.instrument and self._usbtmc_fd is None):
            logger.warning("Oscilloscope not connected")
            return None

        self.stop_acquisition()
        time.sleep(0.05)

        channels = {
            1: self._read_waveform_data(1),
            2: self._read_waveform_data(2),
        }
        if channels[1] is None and channels[2] is None:
            return None

        rows, alignment = self._combine_waveform_rows(channels, stop_elapsed_seconds)
        return {
            "metadata": {
                "idn": self._idn,
                "visa_address": self.visa_address,
                "stop_elapsed_seconds": stop_elapsed_seconds,
                "time_alignment": (
                    "time is relative to measurement t0; final exported sample "
                    "is aligned to the UI/API Stop request"
                ),
                "channels": {
                    str(channel): data["metadata"]
                    for channel, data in channels.items()
                    if data is not None
                },
                **alignment,
            },
            "rows": rows,
        }

    def _read_tektronix_waveform_voltage(
        self,
        channel: int,
        latest_sample: bool = False,
    ) -> Optional[float]:
        """Read a visible waveform segment and return a voltage value."""
        if not self.instrument and self._usbtmc_fd is None:
            return None

        try:
            record_length = int(float(self._query("HORizontal:RECOrdlength?").strip()))
        except Exception:
            record_length = self._WAVEFORM_SAMPLE_POINTS

        stop = max(1, record_length)
        start = stop if latest_sample else max(1, stop - self._WAVEFORM_SAMPLE_POINTS + 1)

        try:
            for command in (
                f"DATa:SOUrce CH{channel}",
                "DATa:WIDTH 1",
                "DATa:ENCdg RIBinary",
                f"DATa:START {start}",
                f"DATa:STOP {stop}",
            ):
                self._write(command)

            preamble = self._query("WFMOutpre?").strip()
            parts = preamble.split(";")
            ymult = float(parts[13])
            yoff = float(parts[14])
            yzero = float(parts[15])
            raw_values = self._query_binary_values("CURVE?")
        except Exception as e:
            logger.debug("Failed to read Tektronix waveform channel %s: %s", channel, e)
            return None

        if not raw_values:
            return None

        if latest_sample:
            value = raw_values[-1]
            voltage = (value - yoff) * ymult + yzero
            return voltage if math.isfinite(voltage) else None

        trailing_values = raw_values[-min(25, len(raw_values)) :]
        voltages = [(value - yoff) * ymult + yzero for value in trailing_values]
        finite_voltages = [value for value in voltages if math.isfinite(value)]
        if not finite_voltages:
            return None

        return sum(finite_voltages) / len(finite_voltages)

    def _read_waveform_data(self, channel: int) -> Optional[dict]:
        """Read the full current waveform record for one channel."""
        if not self.instrument and self._usbtmc_fd is None:
            return None

        try:
            record_length = int(float(self._query("HORizontal:RECOrdlength?").strip()))
        except Exception:
            record_length = self._WAVEFORM_SAMPLE_POINTS

        start = 1
        stop = max(1, record_length)

        try:
            for command in (
                f"DATa:SOUrce CH{channel}",
                "DATa:WIDTH 1",
                "DATa:ENCdg RIBinary",
                f"DATa:START {start}",
                f"DATa:STOP {stop}",
            ):
                self._write(command)

            preamble = self._query("WFMOutpre?").strip()
            parts = preamble.split(";")
            point_count = int(float(parts[6]))
            x_unit = parts[8].strip('"')
            x_increment = float(parts[9])
            x_zero = float(parts[10])
            point_offset = float(parts[11])
            y_unit = parts[12].strip('"')
            y_multiplier = float(parts[13])
            y_offset = float(parts[14])
            y_zero = float(parts[15])
            raw_values = self._query_binary_values("CURVE?")
        except Exception as e:
            logger.error("Failed to export oscilloscope CH%s waveform: %s", channel, e)
            return None

        voltages = [
            (value - y_offset) * y_multiplier + y_zero
            for value in raw_values
        ]
        scope_times = [
            x_zero + ((start - 1 + index) - point_offset) * x_increment
            for index in range(len(voltages))
        ]

        return {
            "voltages": voltages,
            "scope_times": scope_times,
            "x_increment": x_increment,
            "metadata": {
                "channel": channel,
                "data_start": start,
                "data_stop": stop,
                "preamble_point_count": point_count,
                "exported_point_count": len(voltages),
                "x_unit": x_unit,
                "x_increment": x_increment,
                "x_zero": x_zero,
                "point_offset": point_offset,
                "y_unit": y_unit,
                "y_multiplier": y_multiplier,
                "y_offset": y_offset,
                "y_zero": y_zero,
                "preamble": preamble,
            },
        }

    def _combine_waveform_rows(
        self,
        channels: dict[int, Optional[dict]],
        stop_elapsed_seconds: Optional[float],
    ) -> tuple[list[dict], dict]:
        ch1 = channels.get(1)
        ch2 = channels.get(2)
        ch1_values = ch1["voltages"] if ch1 else []
        ch2_values = ch2["voltages"] if ch2 else []
        ch1_times = ch1["scope_times"] if ch1 else []
        ch2_times = ch2["scope_times"] if ch2 else []
        sample_count = max(len(ch1_values), len(ch2_values))

        x_increment = None
        if ch1:
            x_increment = ch1["x_increment"]
        elif ch2:
            x_increment = ch2["x_increment"]

        rows: list[dict] = []
        cropped_before_t0 = 0
        for sample_index in range(sample_count):
            samples_from_end = sample_count - 1 - sample_index
            aligned_time = None
            if stop_elapsed_seconds is not None and x_increment is not None:
                aligned_time = stop_elapsed_seconds - samples_from_end * x_increment
                if aligned_time < 0:
                    cropped_before_t0 += 1
                    continue

            ch1_index = sample_index - (sample_count - len(ch1_values))
            ch2_index = sample_index - (sample_count - len(ch2_values))
            ch1_voltage = ch1_values[ch1_index] if 0 <= ch1_index < len(ch1_values) else None
            ch2_voltage = ch2_values[ch2_index] if 0 <= ch2_index < len(ch2_values) else None
            scope_time = None
            if 0 <= ch1_index < len(ch1_times):
                scope_time = ch1_times[ch1_index]
            elif 0 <= ch2_index < len(ch2_times):
                scope_time = ch2_times[ch2_index]

            rows.append(
                {
                    "time": aligned_time,
                    "scope_time": scope_time,
                    "ch1_voltage": ch1_voltage,
                    "ch2_voltage": ch2_voltage,
                    "sample_index": len(rows),
                    "ch1_sample_index": ch1_index if 0 <= ch1_index < len(ch1_values) else None,
                    "ch2_sample_index": ch2_index if 0 <= ch2_index < len(ch2_values) else None,
                }
            )

        return rows, {
            "exported_rows": len(rows),
            "cropped_rows_before_t0": cropped_before_t0,
            "raw_combined_rows": sample_count,
        }

    def _query_float(self, query: str) -> Optional[float]:
        if not self.instrument and self._usbtmc_fd is None:
            return None

        try:
            response = self._query(query).strip()
        except Exception:
            logger.debug("Oscilloscope query not accepted: %s", query)
            return None

        matches = self._FLOAT_RE.findall(response)
        if not matches:
            return None

        try:
            value = float(matches[-1])
        except ValueError:
            return None

        if not math.isfinite(value) or abs(value) > self._TEKTRONIX_INVALID_THRESHOLD:
            return None

        return value

    def _query_binary_values(self, query: str) -> list[int]:
        if self.instrument:
            return self.instrument.query_binary_values(
                query,
                datatype="b",
                is_big_endian=False,
                container=list,
            )

        block = self._query_binary_block(query)
        return [int.from_bytes(bytes([value]), "little", signed=True) for value in block]

    def _query_binary_block(self, query: str) -> bytes:
        if self._usbtmc_fd is None:
            raise RuntimeError("Oscilloscope not connected")

        os.write(self._usbtmc_fd, f"{query}\n".encode("ascii"))
        header = self._read_usbtmc_exact(2)
        if not header.startswith(b"#"):
            raise RuntimeError(f"Unexpected binary block header: {header!r}")

        length_digits = int(header[1:2].decode("ascii"))
        length = int(self._read_usbtmc_exact(length_digits).decode("ascii"))
        data = self._read_usbtmc_exact(length)

        try:
            self._read_usbtmc_available()
        except Exception:
            pass

        return data

    def _read_usbtmc_exact(self, byte_count: int) -> bytes:
        chunks: list[bytes] = []
        remaining = byte_count
        deadline = time.monotonic() + self._USBTMC_QUERY_TIMEOUT_SECONDS

        while remaining > 0 and time.monotonic() < deadline:
            try:
                chunk = os.read(self._usbtmc_fd, remaining)
            except BlockingIOError:
                select.select([], [], [], 0.02)
                continue

            if not chunk:
                select.select([], [], [], 0.02)
                continue

            chunks.append(chunk)
            remaining -= len(chunk)

        if remaining > 0:
            raise TimeoutError("Oscilloscope binary read timed out")

        return b"".join(chunks)

    def _read_usbtmc_available(self) -> bytes:
        if self._usbtmc_fd is None:
            return b""

        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(self._usbtmc_fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)

        return b"".join(chunks)

    def _write(self, command: str):
        if self._usbtmc_fd is not None:
            os.write(self._usbtmc_fd, f"{command}\n".encode("ascii"))
            return

        if not self.instrument:
            raise RuntimeError("Oscilloscope not connected")

        self.instrument.write(command)

    def _query(self, query: str) -> str:
        if self._usbtmc_fd is not None:
            os.write(self._usbtmc_fd, f"{query}\n".encode("ascii"))
            deadline = time.monotonic() + self._USBTMC_QUERY_TIMEOUT_SECONDS

            while time.monotonic() < deadline:
                try:
                    response = os.read(self._usbtmc_fd, 4096)
                except BlockingIOError:
                    select.select([], [], [], 0.02)
                    continue

                if response:
                    return response.decode("ascii", errors="replace")

            raise TimeoutError(f"Oscilloscope query timed out: {query}")

        if not self.instrument:
            raise RuntimeError("Oscilloscope not connected")

        return str(self.instrument.query(query))

    @classmethod
    def _is_usbtmc_resource(cls, resource: str) -> bool:
        return resource.startswith(cls._USBTMC_PREFIX) or resource.startswith("/dev/usbtmc")

    @classmethod
    def _usbtmc_device_path(cls, resource: str) -> str:
        if resource.startswith("/dev/usbtmc"):
            return resource

        path = resource.removeprefix(cls._USBTMC_PREFIX)
        return path.removesuffix("::INSTR")

    @property
    def _is_tektronix_scope(self) -> bool:
        return "TEKTRONIX" in (self._idn or "").upper()

    @property
    def is_connected(self) -> bool:
        """Check whether the oscilloscope is connected."""
        return self._is_connected
