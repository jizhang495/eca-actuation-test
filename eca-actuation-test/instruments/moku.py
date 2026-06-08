"""Moku:Pro Data Logger driver using mokucli."""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from moku.instruments import Datalogger as MokuApiDatalogger
    from moku.instruments import MultiInstrument as MokuApiMultiInstrument
    from moku.instruments import WaveformGenerator as MokuApiWaveformGenerator
except ImportError:  # pragma: no cover - exercised only on incomplete installs.
    MokuApiDatalogger = None
    MokuApiMultiInstrument = None
    MokuApiWaveformGenerator = None

logger = logging.getLogger(__name__)


class MokuCliError(RuntimeError):
    """Raised when mokucli returns a non-zero exit code."""


class MokuProDatalogger:
    """Moku:Pro two-channel voltage logger.

    This class intentionally uses mokucli instead of importing a Python Moku
    package so the app can work with the user's installed MokuCLI bundle.
    """

    RESOURCE_PREFIX = "MOKU::"
    _CH1_PROBE_ATTENUATION = 10.0
    _CH2_PROBE_ATTENUATION = 1.0
    _CH3_PROBE_ATTENUATION = 1.0
    _CH1_FRONTEND_RANGE = "400mVpp"
    _CH2_FRONTEND_RANGE = "400mVpp"
    _CH3_FRONTEND_RANGE = "400mVpp"
    _CURRENT_MODE_RAW_CH2_SHUNT = "raw_ch2_shunt"
    _CURRENT_MODE_SR551_DIFFERENTIAL = "sr551_differential"
    _SETTING_COMMAND_READ_TIMEOUT_SECONDS = 2.0
    _WAVEFORM_COMMAND_READ_TIMEOUT_SECONDS = 0.5
    _MIM_PLATFORM_ID = 4
    _MIM_OUTPUT_GAIN = "14dB"
    _WAVEFORM_TYPES = {
        "off": "Off",
        "sine": "Sine",
        "square": "Square",
        "ramp": "Ramp",
        "pulse": "Pulse",
    }

    def __init__(self, moku_address: Optional[str] = None):
        self.moku_address = moku_address
        self._is_connected = False
        self._idn: Optional[str] = None
        self._log_file_name: Optional[str] = None
        self._last_sample_rate_hz: Optional[float] = None
        self._last_duration_seconds: Optional[float] = None
        self._instrument = None
        self._mim = None
        self._waveform_generator = None
        self._use_multi_instrument = False
        self._current_mode = self._CURRENT_MODE_RAW_CH2_SHUNT
        self._current_shunt_ohms = 330.0
        self._current_amplifier_gain = 1.0
        self._api_lock = threading.RLock()
        self.last_error: Optional[str] = None
        self.last_logging_start_request_monotonic: Optional[float] = None
        self.last_logging_start_ack_monotonic: Optional[float] = None

    @classmethod
    def list_available_devices(cls) -> list[str]:
        return [device["resource"] for device in cls.discover_devices()]

    @classmethod
    def discover_devices(cls, timeout_seconds: float = 2.0) -> list[dict]:
        """Discover Moku devices with mokucli list."""
        if not shutil.which("mokucli"):
            return []

        try:
            result = subprocess.run(
                ["mokucli", "list", "--timeout", str(timeout_seconds)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(3.0, timeout_seconds + 2.0),
                check=False,
            )
        except Exception as exc:
            logger.warning("Failed to run mokucli list: %s", exc)
            return []

        if result.returncode != 0:
            logger.warning("mokucli list failed: %s", result.stderr.strip())
            return []

        devices = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("-") or stripped.startswith("Name "):
                continue

            parts = stripped.split()
            if len(parts) < 4 or not parts[0].lower().startswith("moku"):
                continue

            name = parts[0]
            serial = parts[1]
            hardware = parts[2]
            firmware = parts[3]
            addresses = parts[4:]
            ipv4 = next((part for part in addresses if re.match(r"^\d+\.\d+\.\d+\.\d+$", part)), None)
            ipv6 = next((part for part in addresses if ":" in part), None)
            cli_address = ipv4 or cls._format_ipv6_for_cli(ipv6) or f"{name}.local"
            resource = f"{cls.RESOURCE_PREFIX}{cli_address}"
            label_address = ipv4 or ipv6 or f"{name}.local"
            devices.append(
                {
                    "resource": resource,
                    "address": cli_address,
                    "idn": f"{name}, serial {serial}, Moku:{hardware}, MokuOS {firmware}",
                    "name": name,
                    "serial": serial,
                    "hardware": hardware,
                    "firmware": firmware,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "label": f"Moku:{hardware} {name} ({label_address})",
                }
            )

        return devices

    @staticmethod
    def _format_ipv6_for_cli(ipv6: Optional[str]) -> Optional[str]:
        if not ipv6:
            return None
        address = ipv6
        if "%" in address:
            host, zone = address.split("%", 1)
            address = f"{host}%25{zone}"
        return f"[{address}]"

    @classmethod
    def normalize_address(cls, address: Optional[str]) -> Optional[str]:
        if not address:
            return None
        if address.startswith(cls.RESOURCE_PREFIX):
            return address[len(cls.RESOURCE_PREFIX) :]
        return address

    def connect(
        self,
        moku_address: Optional[str] = None,
        use_multi_instrument: bool = False,
    ) -> bool:
        if moku_address:
            self.moku_address = self.normalize_address(moku_address)
        else:
            self.moku_address = self.normalize_address(self.moku_address)

        if not self.moku_address:
            self.last_error = "No Moku address specified"
            return False

        if MokuApiDatalogger is None or (
            use_multi_instrument
            and (MokuApiMultiInstrument is None or MokuApiWaveformGenerator is None)
        ):
            self.last_error = (
                "The moku Python package is not installed. Install it with `uv add moku`."
            )
            return False

        try:
            self.last_error = None
            self._use_multi_instrument = use_multi_instrument
            if use_multi_instrument:
                self._mim = MokuApiMultiInstrument(
                    self.moku_address,
                    force_connect=True,
                    persist_state=True,
                    platform_id=self._MIM_PLATFORM_ID,
                )
                self._instrument = self._mim.set_instrument(1, MokuApiDatalogger)
                self._waveform_generator = self._mim.set_instrument(
                    2,
                    MokuApiWaveformGenerator,
                )
            else:
                self._instrument = MokuApiDatalogger(
                    self.moku_address,
                    force_connect=True,
                    persist_state=True,
                )
            serial_source = self._mim if use_multi_instrument else self._instrument
            serial = self._api_call(serial_source.serial_number)
            mode = "Moku:Pro MIM" if use_multi_instrument else "Moku:Pro"
            self._idn = f"{mode} {self.moku_address} serial {serial}"
            self._is_connected = True
            logger.info("Connected to %s: %s", mode, self._idn)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._instrument = None
            self._mim = None
            self._waveform_generator = None
            self._use_multi_instrument = False
            self._is_connected = False
            logger.error("Failed to connect to Moku:Pro at %s: %s", self.moku_address, exc)
            return False

    def configure_voltage_channels(
        self,
        sample_rate_hz: float,
        *,
        current_mode: str = _CURRENT_MODE_RAW_CH2_SHUNT,
        shunt_ohms: float = 330.0,
        amplifier_gain: float = 1.0,
    ):
        """Configure Data Logger voltage inputs and current-conversion metadata."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")
        if current_mode not in {
            self._CURRENT_MODE_RAW_CH2_SHUNT,
            self._CURRENT_MODE_SR551_DIFFERENTIAL,
        }:
            raise ValueError(f"Unsupported Moku current mode: {current_mode}")
        if shunt_ohms <= 0:
            raise ValueError("shunt_ohms must be greater than 0")
        if amplifier_gain <= 0:
            raise ValueError("amplifier_gain must be greater than 0")

        self._current_mode = current_mode
        self._current_shunt_ohms = float(shunt_ohms)
        self._current_amplifier_gain = float(amplifier_gain)

        instrument = self._require_instrument()
        if self._use_multi_instrument:
            self._configure_multi_instrument_routing()
            self._configure_multi_instrument_frontends()

        enable_ch3 = current_mode == self._CURRENT_MODE_SR551_DIFFERENTIAL
        self._api_setting_call(instrument.enable_input, channel=1, enable=True, strict=False)
        self._api_setting_call(instrument.enable_input, channel=2, enable=True, strict=False)
        self._api_setting_call(instrument.enable_input, channel=3, enable=enable_ch3, strict=False)
        self._api_setting_call(instrument.enable_input, channel=4, enable=False, strict=False)
        if not self._use_multi_instrument:
            self._api_setting_call(
                instrument.set_frontend,
                channel=1,
                impedance="1MOhm",
                coupling="DC",
                range=self._CH1_FRONTEND_RANGE,
                strict=False,
            )
            self._api_setting_call(
                instrument.set_frontend,
                channel=2,
                impedance="1MOhm",
                coupling="DC",
                range=self._CH2_FRONTEND_RANGE,
                strict=False,
            )
            if enable_ch3:
                self._api_setting_call(
                    instrument.set_frontend,
                    channel=3,
                    impedance="1MOhm",
                    coupling="DC",
                    range=self._CH3_FRONTEND_RANGE,
                    strict=False,
                )
        self._api_setting_call(instrument.set_acquisition_mode, mode="Normal", strict=False)
        self._api_setting_call(
            instrument.set_samplerate,
            sample_rate=float(sample_rate_hz),
            strict=False,
        )
        self._verify_voltage_channel_summary(
            self._api_call(instrument.summary),
            expected_sample_rate_hz=float(sample_rate_hz),
            expect_ch3=enable_ch3,
        )

        self._last_sample_rate_hz = sample_rate_hz

    def _configure_multi_instrument_routing(self):
        mim = self._require_mim()
        self._api_setting_call(
            mim.set_connections,
            connections=[
                {"source": "Input1", "destination": "Slot1InA"},
                {"source": "Input2", "destination": "Slot1InB"},
                {"source": "Input3", "destination": "Slot1InC"},
                {"source": "Slot2OutA", "destination": "Output1"},
            ],
        )
        self._api_setting_call(
            mim.set_output,
            channel=1,
            output_gain=self._MIM_OUTPUT_GAIN,
            strict=False,
        )

    def _configure_multi_instrument_frontends(self):
        mim = self._require_mim()
        frontend_settings = (
            (1, self._CH1_FRONTEND_RANGE),
            (2, self._CH2_FRONTEND_RANGE),
            (3, self._CH3_FRONTEND_RANGE),
        )
        for channel, frontend_range in frontend_settings:
            self._api_setting_call(
                mim.set_frontend,
                channel=channel,
                impedance="1MOhm",
                coupling="DC",
                gain="0dB",
                attenuation=None,
                bandwidth=None,
                strict=False,
            )

    def start_logging(
        self,
        duration_seconds: float,
        sample_rate_hz: float,
        file_name_prefix: str,
        comments: str = "",
    ) -> dict:
        """Start a Moku Data Logger file recording on the device."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be greater than 0")
        if sample_rate_hz < 10:
            raise ValueError("Moku:Pro sample rate must be at least 10 Sa/s")
        if sample_rate_hz > 1_000_000:
            raise ValueError("Moku:Pro API logging sample rate must be at most 1 MSa/s")

        self.last_logging_start_request_monotonic = time.perf_counter()
        instrument = self._require_instrument()
        response = self._api_call(
            instrument.start_logging,
            duration=int(math.ceil(duration_seconds)),
            sample_rate=float(sample_rate_hz),
            file_name_prefix=self._safe_file_prefix(file_name_prefix),
            comments=comments,
            strict=False,
        )
        self.last_logging_start_ack_monotonic = time.perf_counter()
        if isinstance(response, dict):
            self._log_file_name = response.get("file_name")
            self._last_sample_rate_hz = (
                response.get("rate") or response.get("sample_rate") or sample_rate_hz
            )
            self._last_duration_seconds = response.get("duration") or duration_seconds
            return response

        self._last_sample_rate_hz = sample_rate_hz
        self._last_duration_seconds = duration_seconds
        return {"response": response}

    def stop_logging(self) -> Optional[dict]:
        if not self._is_connected:
            return None
        try:
            response = self._api_call(self._require_instrument().stop_logging)
            return response if isinstance(response, dict) else {"response": response}
        except Exception as exc:
            message = str(exc)
            message_lower = message.lower()
            if (
                "not logging" in message_lower
                or "no logging" in message_lower
                or "no datalogging session" in message_lower
            ):
                logger.info("Moku:Pro logging already stopped")
                return None
            raise

    def logging_progress(self) -> Optional[dict]:
        if not self._is_connected:
            return None
        response = self._api_call(self._require_instrument().logging_progress)
        return response if isinstance(response, dict) else {"response": response}

    def generate_waveform(
        self,
        waveform: str,
        vpp: float,
        frequency_hz: float,
        channel: int = 1,
    ) -> dict:
        """Generate a waveform on a Moku Data Logger output."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")
        if channel != 1:
            raise ValueError("Only Moku Waveform Generator output 1 is supported")
        if vpp < 0:
            raise ValueError("Waveform Generator Vpp must be non-negative")
        if frequency_hz <= 0:
            raise ValueError("Waveform Generator frequency must be greater than 0 Hz")

        waveform_type = self._normalize_waveform_type(waveform)
        return self._generate_waveform_with_verified_timeout(
            channel=channel,
            waveform_type=waveform_type,
            vpp=float(vpp),
            frequency_hz=float(frequency_hz),
        )

    def stop_waveform_generator(self, channel: int = 1) -> dict:
        """Drive the Moku waveform-generator output to 0 V."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")
        if channel != 1:
            raise ValueError("Only Moku Waveform Generator output 1 is supported")

        return self._stop_waveform_with_verified_timeout(channel=channel)

    def generate_signal(
        self,
        waveform: str,
        vpp: float,
        frequency_hz: float,
        channel: int = 1,
    ) -> dict:
        """Compatibility wrapper for older code naming."""
        return self.generate_waveform(waveform, vpp, frequency_hz, channel=channel)

    def stop_signal_generator(self, channel: int = 1) -> dict:
        """Compatibility wrapper for older code naming."""
        return self.stop_waveform_generator(channel=channel)

    def capture_waveforms(
        self,
        session_dir: Path,
        stop_elapsed_seconds: Optional[float],
        t0_offset_seconds: Optional[float],
    ) -> dict:
        """Download, convert, and normalize the latest Moku logger file."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")
        session_dir.mkdir(parents=True, exist_ok=True)

        progress = None
        try:
            progress = self.logging_progress()
            if progress and progress.get("file_name"):
                self._log_file_name = progress["file_name"]
        except Exception as exc:
            logger.warning("Could not read Moku logging progress: %s", exc)

        if not self._log_file_name:
            raise RuntimeError("Moku:Pro did not report a log file name")

        self._run_cli(
            [
                "files",
                "download",
                self.moku_address,
                "--name",
                self._log_file_name,
                "--force",
            ],
            cwd=session_dir,
            timeout=300,
        )
        raw_path = session_dir / self._log_file_name
        self._run_cli(["convert", str(raw_path), "--format", "csv"], cwd=session_dir, timeout=300)
        converted_path = raw_path.with_suffix(".csv")
        if not converted_path.exists():
            raise RuntimeError(f"Moku conversion did not create CSV: {converted_path}")

        rows, source_columns = self._read_converted_csv(converted_path, t0_offset_seconds)
        return {
            "metadata": {
                "idn": self._idn,
                "moku_address": self.moku_address,
                "source": "moku",
                "instrument": "Moku:Pro Data Logger",
                "raw_li_path": str(raw_path),
                "converted_csv_path": str(converted_path),
                "moku_file_name": self._log_file_name,
                "source_columns": source_columns,
                "probe_attenuation": {
                    "ch1": self._CH1_PROBE_ATTENUATION,
                    "ch2": self._CH2_PROBE_ATTENUATION,
                    "ch3": self._CH3_PROBE_ATTENUATION,
                },
                "frontend_ranges": {
                    "ch1": self._CH1_FRONTEND_RANGE,
                    "ch2": self._CH2_FRONTEND_RANGE,
                    "ch3": self._CH3_FRONTEND_RANGE,
                },
                "current_mode": self._current_mode,
                "current_shunt_ohms": self._current_shunt_ohms,
                "current_amplifier_gain": self._current_amplifier_gain,
                "current_scaling": self._current_scaling_description(),
                "voltage_scaling": (
                    "moku_waveform.csv stores circuit voltage; raw Moku input "
                    "columns are multiplied by the configured probe attenuation"
                ),
                "requested_sample_rate_hz": self._last_sample_rate_hz,
                "requested_duration_seconds": self._last_duration_seconds,
                "stop_elapsed_seconds": stop_elapsed_seconds,
                "t0_offset_seconds": t0_offset_seconds,
                "time_alignment": (
                    "time is relative to measurement t0; Moku samples before t0 "
                    "are cropped from the app waveform CSV using the logger start "
                    "acknowledgement time when available"
                ),
            },
            "rows": rows,
        }

    def disconnect(self):
        owner = self._mim if self._mim is not None else self._instrument
        if owner is not None:
            try:
                self._api_call(owner.relinquish_ownership)
            except Exception as exc:
                logger.warning("Failed to relinquish Moku:Pro ownership: %s", exc)
        self._instrument = None
        self._mim = None
        self._waveform_generator = None
        self._use_multi_instrument = False
        self._is_connected = False

    def _current_scaling_description(self) -> str:
        if self._current_mode == self._CURRENT_MODE_SR551_DIFFERENTIAL:
            return (
                "current_mA = (ch2_voltage - ch3_voltage) / "
                "(current_shunt_ohms * current_amplifier_gain) * 1000; "
                "CH2 and CH3 are the two SR551 balanced output legs"
            )
        return "current_mA = ch2_voltage / current_shunt_ohms * 1000"

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @staticmethod
    def _safe_file_prefix(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        return safe or "eca_moku"

    def _command(self, object_name: str, function_name: str, **kwargs):
        args = [
            "command",
            object_name,
            function_name,
            f"--ip={self.moku_address}",
            "--force-connect",
        ]
        args.extend(f"{key}={self._format_value(value)}" for key, value in kwargs.items())
        output = self._run_cli(args, timeout=30)
        return self._parse_command_output(output)

    def _require_instrument(self):
        if self._instrument is None:
            raise RuntimeError("Moku:Pro API instrument is not connected")
        return self._instrument

    def _require_mim(self):
        if self._mim is None:
            raise RuntimeError("Moku:Pro Multi-Instrument API is not connected")
        return self._mim

    def _require_waveform_generator(self):
        if self._waveform_generator is None:
            raise RuntimeError("Moku:Pro Waveform Generator API is not connected")
        return self._waveform_generator

    def _api_call(self, func, *args, **kwargs):
        with self._api_lock:
            return func(*args, **kwargs)

    def _api_call_with_read_timeout(self, read_timeout_seconds: float, func, *args, **kwargs):
        with self._api_lock:
            instrument = self._require_instrument()
            session = getattr(instrument, "session", None)
            if session is None or not hasattr(session, "read_timeout"):
                return func(*args, **kwargs)

            original_timeout = session.read_timeout
            session.read_timeout = read_timeout_seconds
            try:
                return func(*args, **kwargs)
            finally:
                session.read_timeout = original_timeout

    def _api_setting_call(self, func, *args, **kwargs):
        try:
            return self._api_call_with_read_timeout(
                self._SETTING_COMMAND_READ_TIMEOUT_SECONDS,
                func,
                *args,
                **kwargs,
            )
        except Exception as exc:
            if not self._is_timeout_exception(exc):
                raise
            logger.info("Moku setting command timed out; verifying state later: %s", exc)
            return None

    @staticmethod
    def _is_timeout_exception(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        return "timeout" in name or "timed out" in message or "read timeout" in message

    @classmethod
    def _verify_voltage_channel_summary(
        cls,
        summary: str,
        *,
        expected_sample_rate_hz: float,
        expect_ch3: bool = False,
    ):
        input_lines = {
            channel: cls._input_summary_line(summary, channel)
            for channel in (1, 2, 3, 4)
        }

        enabled_channels = (1, 2, 3) if expect_ch3 else (1, 2)
        disabled_channels = (4,) if expect_ch3 else (3, 4)

        for channel in enabled_channels:
            line = input_lines[channel]
            range_ok = "400 mVpp" in line or line.startswith(
                ("Input A ", "Input B ", "Input C ")
            )
            if "(on)" not in line or not range_ok:
                raise RuntimeError(f"Moku Input {channel} was not configured for logging: {line}")

        for channel in disabled_channels:
            line = input_lines[channel]
            if "(off)" not in line:
                raise RuntimeError(f"Moku Input {channel} was not disabled: {line}")

        acquisition_line = cls._acquisition_summary_line(summary)
        if "Normal mode" not in acquisition_line:
            raise RuntimeError(f"Moku acquisition mode was not Normal: {acquisition_line}")

        match = re.search(r",\s*([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)\s*Hz", acquisition_line)
        if not match:
            raise RuntimeError(f"Could not parse Moku acquisition sample rate: {acquisition_line}")

        actual_sample_rate_hz = float(match.group(1))
        if not math.isclose(
            actual_sample_rate_hz,
            expected_sample_rate_hz,
            rel_tol=1e-3,
            abs_tol=1e-6,
        ):
            raise RuntimeError(
                f"Moku sample rate is {actual_sample_rate_hz:g} Hz, "
                f"expected {expected_sample_rate_hz:g} Hz"
            )

    def _generate_waveform_with_verified_timeout(
        self,
        channel: int,
        waveform_type: str,
        vpp: float,
        frequency_hz: float,
    ) -> dict:
        params = {
            "channel": channel,
            "type": waveform_type,
            "frequency": frequency_hz,
            "amplitude": vpp,
            "offset": 0.0,
            "strict": False,
        }
        generator = (
            self._require_waveform_generator()
            if self._use_multi_instrument
            else self._require_instrument()
        )
        try:
            response = self._api_call_with_read_timeout(
                self._WAVEFORM_COMMAND_READ_TIMEOUT_SECONDS,
                generator.generate_waveform,
                **params,
            )
            summary = self._api_call(generator.summary)
            self._verify_waveform_summary(
                summary,
                channel=channel,
                expected_enabled=True,
                expected_waveform=waveform_type,
                expected_vpp=vpp,
                expected_frequency_hz=frequency_hz,
            )
            result = response if isinstance(response, dict) else {"response": response}
            result["verification_summary"] = summary
            return result
        except Exception as exc:
            summary = self._verified_summary_after_waveform_exception(
                exc,
                channel=channel,
                expected_enabled=True,
                expected_waveform=waveform_type,
                expected_vpp=vpp,
                expected_frequency_hz=frequency_hz,
            )
            return {
                "status": "verified_after_timeout",
                "warning": str(exc),
                "verification_summary": summary,
            }

    def _stop_waveform_with_verified_timeout(self, channel: int) -> dict:
        generator = (
            self._require_waveform_generator()
            if self._use_multi_instrument
            else self._require_instrument()
        )
        try:
            response = self._api_call_with_read_timeout(
                self._WAVEFORM_COMMAND_READ_TIMEOUT_SECONDS,
                generator.generate_waveform,
                channel=channel,
                type="Off",
                strict=False,
            )
            summary = self._api_call(generator.summary)
            self._verify_waveform_summary(
                summary,
                channel=channel,
                expected_enabled=False,
            )
            result = response if isinstance(response, dict) else {"response": response}
            result["verification_summary"] = summary
            return result
        except Exception as exc:
            summary = self._verified_summary_after_waveform_exception(
                exc,
                channel=channel,
                expected_enabled=False,
            )
            return {
                "status": "verified_after_timeout",
                "warning": str(exc),
                "verification_summary": summary,
            }

    def _verified_summary_after_waveform_exception(
        self,
        exc: Exception,
        *,
        channel: int,
        expected_enabled: bool,
        expected_waveform: Optional[str] = None,
        expected_vpp: Optional[float] = None,
        expected_frequency_hz: Optional[float] = None,
    ) -> str:
        try:
            generator = (
                self._require_waveform_generator()
                if self._use_multi_instrument
                else self._require_instrument()
            )
            summary = self._api_call(generator.summary)
            self._verify_waveform_summary(
                summary,
                channel=channel,
                expected_enabled=expected_enabled,
                expected_waveform=expected_waveform,
                expected_vpp=expected_vpp,
                expected_frequency_hz=expected_frequency_hz,
            )
            logger.info("Moku waveform command timed out but verified output state: %s", exc)
            return summary
        except Exception:
            raise exc

    @classmethod
    def _verify_waveform_summary(
        cls,
        summary: str,
        *,
        channel: int,
        expected_enabled: bool,
        expected_waveform: Optional[str] = None,
        expected_vpp: Optional[float] = None,
        expected_frequency_hz: Optional[float] = None,
    ):
        line = cls._output_summary_line(summary, channel)
        expected_state = "on" if expected_enabled else "off"
        output_label = str(channel)
        if line.startswith("Output A "):
            output_label = "A"
        elif line.startswith("Output B "):
            output_label = "B"
        elif line.startswith("Output C "):
            output_label = "C"
        elif line.startswith("Output D "):
            output_label = "D"
        state_match = re.search(rf"^Output {output_label} \((on|off)\) - ([A-Za-z]+)", line)
        if not state_match:
            raise RuntimeError(f"Could not parse Moku output summary line: {line}")

        actual_state = state_match.group(1)
        actual_waveform = state_match.group(2)
        if actual_state != expected_state:
            raise RuntimeError(
                f"Moku Output {channel} state is {actual_state}, expected {expected_state}: {line}"
            )

        if not expected_enabled:
            return

        if expected_waveform and actual_waveform.lower() != expected_waveform.lower():
            raise RuntimeError(
                f"Moku Output {channel} waveform is {actual_waveform}, "
                f"expected {expected_waveform}: {line}"
            )

        segments = [segment.strip() for segment in line.split(",")]
        if expected_frequency_hz is not None and len(segments) > 1:
            actual_frequency_hz = cls._parse_summary_quantity(segments[1])
            if actual_frequency_hz is None or not math.isclose(
                actual_frequency_hz,
                expected_frequency_hz,
                rel_tol=1e-3,
                abs_tol=1e-6,
            ):
                raise RuntimeError(
                    f"Moku Output {channel} frequency is {segments[1]}, "
                    f"expected {expected_frequency_hz:g} Hz"
                )

        if expected_vpp is not None and len(segments) > 2:
            actual_vpp = cls._parse_summary_quantity(segments[2])
            if actual_vpp is None or not math.isclose(
                actual_vpp,
                expected_vpp,
                rel_tol=1e-3,
                abs_tol=1e-6,
            ):
                raise RuntimeError(
                    f"Moku Output {channel} amplitude is {segments[2]}, "
                    f"expected {expected_vpp:g} Vpp"
                )

    @staticmethod
    def _output_summary_line(summary: str, channel: int) -> str:
        prefixes = [f"Output {channel} "]
        if channel == 1:
            prefixes.append("Output A ")
        elif channel == 2:
            prefixes.append("Output B ")
        elif channel == 3:
            prefixes.append("Output C ")
        elif channel == 4:
            prefixes.append("Output D ")
        for line in summary.splitlines():
            if any(line.startswith(prefix) for prefix in prefixes):
                return line
        raise RuntimeError(f"Moku summary did not include Output {channel}")

    @staticmethod
    def _input_summary_line(summary: str, channel: int) -> str:
        prefixes = [f"Input {channel} "]
        if channel == 1:
            prefixes.append("Input A ")
        elif channel == 2:
            prefixes.append("Input B ")
        elif channel == 3:
            prefixes.append("Input C ")
        elif channel == 4:
            prefixes.append("Input D ")
        for line in summary.splitlines():
            if any(line.startswith(prefix) for prefix in prefixes):
                return line
        raise RuntimeError(f"Moku summary did not include Input {channel}")

    @staticmethod
    def _acquisition_summary_line(summary: str) -> str:
        for line in summary.splitlines():
            if line.startswith("Acquisition:"):
                return line
        raise RuntimeError("Moku summary did not include acquisition settings")

    @staticmethod
    def _parse_summary_quantity(value: str) -> Optional[float]:
        compact = re.sub(r"(?<=\d)\s+(?=\d)", "", value.strip())
        match = re.match(r"([-+]?\d+(?:\.\d+)?)\s*([A-Za-z]+)", compact)
        if not match:
            return None

        number = float(match.group(1))
        unit = match.group(2).lower()
        if unit in {"hz", "vpp", "v"}:
            return number
        if unit in {"khz", "kvpp", "kv"}:
            return number * 1_000
        if unit in {"mhz", "mvpp", "mv"}:
            multiplier = 1_000_000 if unit == "mhz" else 1e-3
            return number * multiplier
        if unit in {"uvpp", "uv"}:
            return number * 1e-6
        return None

    @staticmethod
    def _format_value(value: object) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)

    @classmethod
    def _normalize_waveform_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in cls._WAVEFORM_TYPES:
            raise ValueError(f"Unsupported Moku waveform type: {value}")
        return cls._WAVEFORM_TYPES[normalized]

    @staticmethod
    def _parse_command_output(output: str):
        text = output.strip()
        if not text:
            return None

        candidates = [text]
        object_start = text.find("{")
        array_start = text.find("[")
        if object_start >= 0:
            candidates.append(text[object_start:])
        if array_start >= 0:
            candidates.append(text[array_start:])

        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        return text

    @staticmethod
    def _run_cli(args: list[str], cwd: Optional[Path] = None, timeout: float = 30) -> str:
        if not shutil.which("mokucli"):
            raise MokuCliError(
                "mokucli is not installed. Install it from "
                "https://apis.liquidinstruments.com/cli/getting-started/install.html"
            )

        result = subprocess.run(
            ["mokucli", *args],
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise MokuCliError(detail or f"mokucli failed with exit code {result.returncode}")

        return result.stdout

    def _read_converted_csv(
        self,
        csv_path: Path,
        t0_offset_seconds: Optional[float],
    ) -> tuple[list[dict], dict]:
        data = self._read_moku_csv_table(csv_path)
        if data.empty:
            raise RuntimeError(f"Moku converted CSV contains no rows: {csv_path}")

        for column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

        time_column = self._find_column(data, ("time", "timestamp", "seconds"))
        numeric_columns = [
            column for column in data.columns if pd.api.types.is_numeric_dtype(data[column])
        ]
        if time_column is None:
            time_column = numeric_columns[0]

        signal_columns = [column for column in numeric_columns if column != time_column]
        ch1_column = self._find_column(
            data,
            ("ch1", "channel1", "input1", "probea", "input a", "input_a"),
            exclude={time_column},
        )
        ch2_column = self._find_column(
            data,
            ("ch2", "channel2", "input2", "probeb", "input b", "input_b"),
            exclude={time_column},
        )
        ch3_column = self._find_column(
            data,
            ("ch3", "channel3", "input3", "probec", "input c", "input_c"),
            exclude={time_column},
        )

        if ch1_column is None and signal_columns:
            ch1_column = signal_columns[0]
        if ch2_column is None and len(signal_columns) > 1:
            ch2_column = signal_columns[1]
        if ch1_column is None or ch2_column is None:
            raise RuntimeError(
                "Could not identify two voltage channels in Moku converted CSV; "
                f"columns are {list(data.columns)}"
            )
        if (
            self._current_mode == self._CURRENT_MODE_SR551_DIFFERENTIAL
            and ch3_column is None
            and len(signal_columns) > 2
        ):
            ch3_column = signal_columns[2]

        offset = t0_offset_seconds or 0.0
        rows: list[dict] = []
        source_time0 = None
        ch1_attenuation = self._CH1_PROBE_ATTENUATION
        ch2_attenuation = self._CH2_PROBE_ATTENUATION
        ch3_attenuation = self._CH3_PROBE_ATTENUATION
        required_columns = [time_column, ch1_column, ch2_column]
        if self._current_mode == self._CURRENT_MODE_SR551_DIFFERENTIAL:
            if ch3_column is None:
                raise RuntimeError(
                    "SR551 differential current mode requires Moku Input 3, but "
                    f"converted columns are {list(data.columns)}"
                )
            required_columns.append(ch3_column)

        for index, row in data[required_columns].dropna().iterrows():
            source_time = float(row[time_column])
            source_time0 = source_time if source_time0 is None else source_time0
            scope_time = source_time - source_time0
            aligned_time = scope_time - offset
            if aligned_time < 0:
                continue
            ch1_voltage = float(row[ch1_column]) * ch1_attenuation
            ch2_voltage = float(row[ch2_column]) * ch2_attenuation
            ch3_voltage = (
                float(row[ch3_column]) * ch3_attenuation if ch3_column is not None else None
            )
            if (
                self._current_mode == self._CURRENT_MODE_SR551_DIFFERENTIAL
                and ch3_voltage is not None
            ):
                current_ma = (
                    (ch2_voltage - ch3_voltage)
                    / (self._current_shunt_ohms * self._current_amplifier_gain)
                    * 1000.0
                )
            else:
                current_ma = ch2_voltage / self._current_shunt_ohms * 1000.0
            rows.append(
                {
                    "time": aligned_time,
                    "scope_time": scope_time,
                    "ch1_voltage": ch1_voltage,
                    "ch2_voltage": ch2_voltage,
                    "ch3_voltage": ch3_voltage,
                    "current_mA": current_ma,
                    "sample_index": len(rows),
                    "ch1_sample_index": int(index),
                    "ch2_sample_index": int(index),
                    "ch3_sample_index": int(index) if ch3_column is not None else None,
                }
            )

        if not rows:
            raise RuntimeError("Moku converted CSV has no samples at or after measurement t0")

        source_columns = {"time": time_column, "ch1": ch1_column, "ch2": ch2_column}
        if ch3_column is not None:
            source_columns["ch3"] = ch3_column
        return rows, source_columns

    @staticmethod
    def _read_moku_csv_table(csv_path: Path) -> pd.DataFrame:
        """Read a Moku converted CSV, skipping '%' metadata lines before the table."""
        lines = csv_path.read_text().splitlines()
        header_index = None
        for index, line in enumerate(lines):
            candidate = line.strip().lstrip("%").strip()
            if "," in candidate and "time" in candidate.lower():
                header_index = index
                break

        if header_index is None:
            return pd.read_csv(csv_path, comment="%")

        header = lines[header_index].strip().lstrip("%").strip()
        table_text = "\n".join([header, *lines[header_index + 1 :]])
        return pd.read_csv(io.StringIO(table_text), skipinitialspace=True)

    @staticmethod
    def _find_column(
        data: pd.DataFrame,
        candidates: tuple[str, ...],
        exclude: Optional[set[str]] = None,
    ) -> Optional[str]:
        exclude = exclude or set()
        for column in data.columns:
            if column in exclude:
                continue
            normalized = re.sub(r"[^a-z0-9]+", "", str(column).lower())
            for candidate in candidates:
                if re.sub(r"[^a-z0-9]+", "", candidate.lower()) in normalized:
                    return column
        return None
