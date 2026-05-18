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
import time
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MokuCliError(RuntimeError):
    """Raised when mokucli returns a non-zero exit code."""


class MokuProDatalogger:
    """Moku:Pro two-channel voltage logger.

    This class intentionally uses mokucli instead of importing a Python Moku
    package so the app can work with the user's installed MokuCLI bundle.
    """

    RESOURCE_PREFIX = "MOKU::"

    def __init__(self, moku_address: Optional[str] = None):
        self.moku_address = moku_address
        self._is_connected = False
        self._idn: Optional[str] = None
        self._log_file_name: Optional[str] = None
        self._last_sample_rate_hz: Optional[float] = None
        self._last_duration_seconds: Optional[float] = None
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

    def connect(self, moku_address: Optional[str] = None) -> bool:
        if moku_address:
            self.moku_address = self.normalize_address(moku_address)
        else:
            self.moku_address = self.normalize_address(self.moku_address)

        if not self.moku_address:
            self.last_error = "No Moku address specified"
            return False

        try:
            self.last_error = None
            serial = self._command("Moku", "serial_number")
            self._idn = f"Moku:Pro {self.moku_address} serial {serial}"
            self._is_connected = True
            logger.info("Connected to Moku:Pro: %s", self._idn)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._is_connected = False
            logger.error("Failed to connect to Moku:Pro at %s: %s", self.moku_address, exc)
            return False

    def configure_voltage_channels(self, sample_rate_hz: float):
        """Configure Data Logger inputs 1 and 2 for DC voltage logging."""
        if not self._is_connected:
            raise RuntimeError("Moku:Pro not connected")

        for command in (
            ("Datalogger", "enable_input", {"channel": 1, "enable": True}),
            ("Datalogger", "enable_input", {"channel": 2, "enable": True}),
            ("Datalogger", "enable_input", {"channel": 3, "enable": False}),
            ("Datalogger", "enable_input", {"channel": 4, "enable": False}),
            (
                "Datalogger",
                "set_frontend",
                {
                    "channel": 1,
                    "impedance": "1MOhm",
                    "coupling": "DC",
                    "range": "4Vpp",
                    "strict": False,
                },
            ),
            (
                "Datalogger",
                "set_frontend",
                {
                    "channel": 2,
                    "impedance": "1MOhm",
                    "coupling": "DC",
                    "range": "400mVpp",
                    "strict": False,
                },
            ),
            ("Datalogger", "set_acquisition_mode", {"mode": "Normal", "strict": False}),
            (
                "Datalogger",
                "set_samplerate",
                {"sample_rate": float(sample_rate_hz), "strict": False},
            ),
        ):
            object_name, function_name, kwargs = command
            self._command(object_name, function_name, **kwargs)

        self._last_sample_rate_hz = sample_rate_hz

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
        response = self._command(
            "Datalogger",
            "start_logging",
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
            response = self._command("Datalogger", "stop_logging")
            return response if isinstance(response, dict) else {"response": response}
        except MokuCliError as exc:
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
        response = self._command("Datalogger", "logging_progress")
        return response if isinstance(response, dict) else {"response": response}

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
                "requested_sample_rate_hz": self._last_sample_rate_hz,
                "requested_duration_seconds": self._last_duration_seconds,
                "stop_elapsed_seconds": stop_elapsed_seconds,
                "t0_offset_seconds": t0_offset_seconds,
                "time_alignment": (
                    "time is relative to measurement t0; Moku samples before t0 "
                    "are cropped from the app waveform CSV"
                ),
            },
            "rows": rows,
        }

    def disconnect(self):
        self._is_connected = False

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

    @staticmethod
    def _format_value(value: object) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)

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

    @classmethod
    def _read_converted_csv(
        cls,
        csv_path: Path,
        t0_offset_seconds: Optional[float],
    ) -> tuple[list[dict], dict]:
        data = cls._read_moku_csv_table(csv_path)
        if data.empty:
            raise RuntimeError(f"Moku converted CSV contains no rows: {csv_path}")

        for column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

        time_column = cls._find_column(data, ("time", "timestamp", "seconds"))
        numeric_columns = [
            column for column in data.columns if pd.api.types.is_numeric_dtype(data[column])
        ]
        if time_column is None:
            time_column = numeric_columns[0]

        signal_columns = [column for column in numeric_columns if column != time_column]
        ch1_column = cls._find_column(
            data,
            ("ch1", "channel1", "input1", "probea", "input a", "input_a"),
            exclude={time_column},
        )
        ch2_column = cls._find_column(
            data,
            ("ch2", "channel2", "input2", "probeb", "input b", "input_b"),
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

        offset = t0_offset_seconds or 0.0
        rows: list[dict] = []
        source_time0 = None
        for index, row in data[[time_column, ch1_column, ch2_column]].dropna().iterrows():
            source_time = float(row[time_column])
            source_time0 = source_time if source_time0 is None else source_time0
            scope_time = source_time - source_time0
            aligned_time = scope_time - offset
            if aligned_time < 0:
                continue
            rows.append(
                {
                    "time": aligned_time,
                    "scope_time": scope_time,
                    "ch1_voltage": float(row[ch1_column]),
                    "ch2_voltage": float(row[ch2_column]),
                    "sample_index": len(rows),
                    "ch1_sample_index": int(index),
                    "ch2_sample_index": int(index),
                }
            )

        if not rows:
            raise RuntimeError("Moku converted CSV has no samples at or after measurement t0")

        return rows, {"time": time_column, "ch1": ch1_column, "ch2": ch2_column}

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
