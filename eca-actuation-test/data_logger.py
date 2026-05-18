"""Data logging and session management."""

import csv
import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
from collections import deque
from queue import Empty, Queue
import threading
import time

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "user-data" / "sessions"
MAX_SESSION_DATA_POINTS = 10000
CSV_FLUSH_EVERY_ROWS = 100
CSV_FLUSH_INTERVAL_SECONDS = 0.5
OSCILLOSCOPE_WAVEFORM_FIELDNAMES = [
    "time",
    "scope_time",
    "ch1_voltage",
    "ch2_voltage",
    "sample_index",
    "ch1_sample_index",
    "ch2_sample_index",
]


def _resolve_data_dir(base_data_dir: str | Path | None = None) -> Path:
    """Resolve session storage to an absolute, stable path."""
    configured_dir = base_data_dir or os.getenv("ECA_DATA_DIR")
    if not configured_dir:
        return DEFAULT_DATA_DIR

    data_dir = Path(configured_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir

    return data_dir


class DataLogger:
    """
    Manages data logging for measurement sessions.
    
    Handles CSV logging of DMM readings and session metadata.
    """

    def __init__(self, base_data_dir: str | Path | None = None):
        """
        Initialize data logger.

        Args:
            base_data_dir: Optional base directory for storing session data. Relative
                paths resolve from the repository root. Defaults to ECA_DATA_DIR, or
                user-data/sessions when the environment variable is not set.
        """
        self.base_data_dir = _resolve_data_dir(base_data_dir)
        self.base_data_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_session_dir: Optional[Path] = None
        self.csv_file: Optional[Path] = None
        self.oscilloscope_waveform_file: Optional[Path] = None
        self.oscilloscope_waveform_metadata_file: Optional[Path] = None
        self.moku_waveform_file: Optional[Path] = None
        self.moku_waveform_metadata_file: Optional[Path] = None
        self.log_file: Optional[Path] = None
        self.config_file: Optional[Path] = None
        
        self._data_queue: Queue = Queue()
        self._logging_active = False
        self._writer_thread: Optional[threading.Thread] = None
        
        self.session_data = deque(maxlen=MAX_SESSION_DATA_POINTS)

    def create_session(self, test_name: str = "test") -> str:
        """
        Create a new measurement session.

        Args:
            test_name: Name for this test session

        Returns:
            Session ID (directory name)
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_id = f"{timestamp}_{test_name}"
        
        self.current_session_dir = self.base_data_dir / session_id
        self.current_session_dir.mkdir(exist_ok=True)
        
        self.csv_file = self.current_session_dir / "readings.csv"
        self.oscilloscope_waveform_file = self.current_session_dir / "oscilloscope_waveform.csv"
        self.oscilloscope_waveform_metadata_file = (
            self.current_session_dir / "oscilloscope_waveform_metadata.json"
        )
        self.moku_waveform_file = self.current_session_dir / "moku_waveform.csv"
        self.moku_waveform_metadata_file = self.current_session_dir / "moku_waveform_metadata.json"
        self.log_file = self.current_session_dir / "log.txt"
        self.config_file = self.current_session_dir / "config.json"
        
        # Initialize CSV with header
        with open(self.csv_file, 'w') as f:
            f.write(
                "time,dmm1_voltage,dmm2_voltage,sample_index,"
                "read_duration_ms,loop_duration_ms,late_by_ms,overrun\n"
            )
        
        self.session_data = deque(maxlen=MAX_SESSION_DATA_POINTS)
        
        logger.info(f"Created new session: {session_id}")
        return session_id

    def save_config(self, config: dict):
        """
        Save session configuration to JSON.

        Args:
            config: Configuration dictionary containing voltage stages, relay stages, etc.
        """
        if not self.config_file:
            logger.warning("No active session to save config")
            return

        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Session config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def log_reading(
        self,
        time_s: float,
        dmm1_voltage: Optional[float],
        dmm2_voltage: Optional[float],
        sample_index: int,
        read_duration_ms: float,
        loop_duration_ms: float,
        late_by_ms: float,
        overrun: bool,
    ):
        """
        Log a reading to the data queue.

        Args:
            time_s: Time in seconds since start
            dmm1_voltage: DMM1 voltage reading
            dmm2_voltage: DMM2 voltage reading
            sample_index: Zero-based acquired sample index
            read_duration_ms: Time spent reading instruments
            loop_duration_ms: Total acquisition loop duration
            late_by_ms: How late this sample started relative to schedule
            overrun: True when loop duration exceeded requested interval
        """
        if not self._logging_active:
            return

        data_point = {
            'time': time_s,
            'dmm1_voltage': dmm1_voltage,
            'dmm2_voltage': dmm2_voltage,
            'sample_index': sample_index,
            'read_duration_ms': read_duration_ms,
            'loop_duration_ms': loop_duration_ms,
            'late_by_ms': late_by_ms,
            'overrun': overrun,
        }
        
        self._data_queue.put(data_point)
        self.session_data.append(data_point)

    def start_logging(self):
        """Start the logging writer thread."""
        if self._logging_active:
            logger.warning("Logging already active")
            return

        if not self.csv_file:
            logger.error("No active session for logging")
            return

        self._logging_active = True
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        logger.info("Data logging started")

    def _writer_loop(self):
        """Background thread for writing data to CSV."""
        try:
            with open(self.csv_file, 'a') as f:
                rows_since_flush = 0
                last_flush = time.monotonic()

                while self._logging_active or not self._data_queue.empty():
                    try:
                        data_point = self._data_queue.get(timeout=0.1)
                        
                        line = (
                            f"{data_point['time']:.6f},"
                            f"{self._format_optional_float(data_point['dmm1_voltage'], 9)},"
                            f"{self._format_optional_float(data_point['dmm2_voltage'], 9)},"
                            f"{data_point['sample_index']},"
                            f"{data_point['read_duration_ms']:.3f},"
                            f"{data_point['loop_duration_ms']:.3f},"
                            f"{data_point['late_by_ms']:.3f},"
                            f"{int(data_point['overrun'])}\n"
                        )
                        f.write(line)
                        rows_since_flush += 1

                        now = time.monotonic()
                        if (
                            rows_since_flush >= CSV_FLUSH_EVERY_ROWS
                            or now - last_flush >= CSV_FLUSH_INTERVAL_SECONDS
                        ):
                            f.flush()
                            rows_since_flush = 0
                            last_flush = now
                        
                    except Empty:
                        if rows_since_flush:
                            f.flush()
                            rows_since_flush = 0
                            last_flush = time.monotonic()
                        continue

                f.flush()
                        
        except Exception as e:
            logger.error(f"Error in writer loop: {e}")

    def save_oscilloscope_waveform(self, waveform: dict) -> tuple[Optional[Path], Optional[Path]]:
        """Save an exported oscilloscope waveform to the active session."""
        if not self.current_session_dir:
            logger.warning("No active session to save oscilloscope waveform")
            return None, None

        csv_path = self.oscilloscope_waveform_file or (
            self.current_session_dir / "oscilloscope_waveform.csv"
        )
        metadata_path = self.oscilloscope_waveform_metadata_file or (
            self.current_session_dir / "oscilloscope_waveform_metadata.json"
        )

        metadata = waveform.get("metadata", {})

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=OSCILLOSCOPE_WAVEFORM_FIELDNAMES,
            )
            writer.writeheader()
            rows = waveform.get("rows")
            if rows is not None:
                rows_written = 0
                first_time = None
                last_time = None
                for row in rows:
                    writer.writerow(row)
                    rows_written += 1
                    time_value = row.get("time")
                    if isinstance(time_value, (int, float)):
                        first_time = time_value if first_time is None else min(first_time, time_value)
                        last_time = time_value if last_time is None else max(last_time, time_value)
                metadata.setdefault("exported_rows", rows_written)
                if first_time is not None and last_time is not None:
                    metadata.setdefault("first_aligned_time", first_time)
                    metadata.setdefault("last_aligned_time", last_time)
                    metadata.setdefault("waveform_coverage_seconds", max(0.0, last_time - first_time))
            else:
                self._write_streamed_oscilloscope_rows(writer, waveform, metadata)

        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info("Oscilloscope waveform saved: %s", csv_path)
        return csv_path, metadata_path

    def save_moku_waveform(self, waveform: dict) -> tuple[Optional[Path], Optional[Path]]:
        """Save an exported Moku waveform to the active session."""
        if not self.current_session_dir:
            logger.warning("No active session to save Moku waveform")
            return None, None

        original_csv_path = self.oscilloscope_waveform_file
        original_metadata_path = self.oscilloscope_waveform_metadata_file
        try:
            self.oscilloscope_waveform_file = self.moku_waveform_file or (
                self.current_session_dir / "moku_waveform.csv"
            )
            self.oscilloscope_waveform_metadata_file = self.moku_waveform_metadata_file or (
                self.current_session_dir / "moku_waveform_metadata.json"
            )
            return self.save_oscilloscope_waveform(waveform)
        finally:
            self.oscilloscope_waveform_file = original_csv_path
            self.oscilloscope_waveform_metadata_file = original_metadata_path

    def _write_streamed_oscilloscope_rows(
        self,
        writer: csv.DictWriter,
        waveform: dict,
        metadata: dict,
    ) -> None:
        """Write raw oscilloscope channel data without building row dicts in memory."""
        channels = waveform.get("channels", {})
        ch1 = channels.get(1) or channels.get("1")
        ch2 = channels.get(2) or channels.get("2")
        ch1_values = ch1.get("raw_values", []) if ch1 else []
        ch2_values = ch2.get("raw_values", []) if ch2 else []
        sample_count = max(len(ch1_values), len(ch2_values))

        x_increment = None
        if ch1:
            x_increment = ch1.get("x_increment")
        elif ch2:
            x_increment = ch2.get("x_increment")

        stop_elapsed_seconds = metadata.get("stop_elapsed_seconds")
        rows_written = 0
        cropped_before_t0 = 0
        first_time = None
        last_time = None

        for sample_index in range(sample_count):
            samples_from_end = sample_count - 1 - sample_index
            aligned_time = None
            if (
                isinstance(stop_elapsed_seconds, (int, float))
                and isinstance(x_increment, (int, float))
            ):
                aligned_time = stop_elapsed_seconds - samples_from_end * x_increment
                if aligned_time < 0:
                    cropped_before_t0 += 1
                    continue

            ch1_index = sample_index - (sample_count - len(ch1_values))
            ch2_index = sample_index - (sample_count - len(ch2_values))
            scope_time = None
            if ch1 and 0 <= ch1_index < len(ch1_values):
                scope_time = self._oscilloscope_scope_time(ch1, ch1_index)
            elif ch2 and 0 <= ch2_index < len(ch2_values):
                scope_time = self._oscilloscope_scope_time(ch2, ch2_index)

            writer.writerow(
                {
                    "time": aligned_time,
                    "scope_time": scope_time,
                    "ch1_voltage": (
                        self._oscilloscope_voltage(ch1, ch1_index)
                        if ch1 and 0 <= ch1_index < len(ch1_values)
                        else None
                    ),
                    "ch2_voltage": (
                        self._oscilloscope_voltage(ch2, ch2_index)
                        if ch2 and 0 <= ch2_index < len(ch2_values)
                        else None
                    ),
                    "sample_index": rows_written,
                    "ch1_sample_index": ch1_index if 0 <= ch1_index < len(ch1_values) else None,
                    "ch2_sample_index": ch2_index if 0 <= ch2_index < len(ch2_values) else None,
                }
            )
            rows_written += 1

            if isinstance(aligned_time, (int, float)):
                first_time = aligned_time if first_time is None else min(first_time, aligned_time)
                last_time = aligned_time if last_time is None else max(last_time, aligned_time)

        metadata["exported_rows"] = rows_written
        metadata["cropped_rows_before_t0"] = cropped_before_t0
        metadata["raw_combined_rows"] = sample_count
        if first_time is not None and last_time is not None:
            metadata["first_aligned_time"] = first_time
            metadata["last_aligned_time"] = last_time
            metadata["waveform_coverage_seconds"] = max(0.0, last_time - first_time)

    @staticmethod
    def _oscilloscope_voltage(channel: dict, sample_index: int) -> Optional[float]:
        metadata = channel.get("metadata", {})
        raw_value = channel["raw_values"][sample_index]
        voltage = (
            (raw_value - metadata.get("y_offset", 0.0))
            * metadata.get("y_multiplier", 1.0)
            + metadata.get("y_zero", 0.0)
        )
        return voltage if math.isfinite(voltage) else None

    @staticmethod
    def _oscilloscope_scope_time(channel: dict, sample_index: int) -> Optional[float]:
        metadata = channel.get("metadata", {})
        x_increment = metadata.get("x_increment")
        x_zero = metadata.get("x_zero")
        point_offset = metadata.get("point_offset", 0.0)
        data_start = metadata.get("data_start", 1)
        if not isinstance(x_increment, (int, float)) or not isinstance(x_zero, (int, float)):
            return None

        return x_zero + ((data_start - 1 + sample_index) - point_offset) * x_increment

    @staticmethod
    def _format_optional_float(value: Optional[float], digits: int) -> str:
        if value is None:
            return ""
        return f"{value:.{digits}f}"

    def stop_logging(self):
        """Stop logging and wait for writer thread to finish."""
        if not self._logging_active:
            return

        self._logging_active = False
        
        if self._writer_thread:
            self._writer_thread.join(timeout=5.0)
            self._writer_thread = None
        
        logger.info("Data logging stopped")

    def append_log(self, message: str):
        """
        Append a message to the session log file.

        Args:
            message: Log message to append
        """
        if not self.log_file:
            return

        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with open(self.log_file, 'a') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            logger.error(f"Failed to write to log file: {e}")

    def get_session_data(self) -> list[dict]:
        """
        Get all data points from current session.

        Returns:
            List of data points
        """
        return list(self.session_data)

    def get_recent_data(self, limit: int = MAX_SESSION_DATA_POINTS) -> list[dict]:
        """
        Get the latest in-memory data points for live browser backfill.

        Args:
            limit: Maximum number of points to return.

        Returns:
            Latest data points from the current process memory.
        """
        data = list(self.session_data)
        if limit <= 0:
            return []
        return data[-limit:]

    def get_session_dataframe(self) -> Optional[pd.DataFrame]:
        """
        Get session data as pandas DataFrame.

        Returns:
            DataFrame with session data, or None if no data
        """
        if not self.session_data:
            return None

        return pd.DataFrame(list(self.session_data))

    def list_sessions(self) -> list[str]:
        """
        List all available sessions.

        Returns:
            List of session IDs
        """
        try:
            sessions = [d.name for d in self.base_data_dir.iterdir() if d.is_dir()]
            return sorted(sessions, reverse=True)  # Most recent first
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []

    def get_session_info(self, session_id: str) -> Optional[dict]:
        """
        Get information about a specific session.

        Args:
            session_id: Session ID to query

        Returns:
            Dictionary with session info, or None if not found
        """
        session_dir = self.base_data_dir / session_id
        if not session_dir.exists():
            return None

        info = {
            'session_id': session_id,
            'path': str(session_dir),
            'files': []
        }

        # List files in session
        for file_path in session_dir.iterdir():
            if file_path.is_file():
                info['files'].append({
                    'name': file_path.name,
                    'size': file_path.stat().st_size,
                    'modified': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                })

        # Read config if available
        config_path = session_dir / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    info['config'] = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config: {e}")

        return info
