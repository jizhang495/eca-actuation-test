"""Data logging and session management."""

import json
import logging
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
            'dmm1_voltage': dmm1_voltage if dmm1_voltage is not None else 0.0,
            'dmm2_voltage': dmm2_voltage if dmm2_voltage is not None else 0.0,
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
                            f"{data_point['dmm1_voltage']:.9f},"
                            f"{data_point['dmm2_voltage']:.9f},"
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
