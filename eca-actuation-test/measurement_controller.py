"""Main measurement controller coordinating all instruments."""

import asyncio
import json
import logging
import math
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional
import pyvisa

from instruments import (
    KeithleyDMM,
    MokuProDatalogger,
    Oscilloscope,
    IT6412PowerSupply,
    USB_RLY08C,
)
from instruments.mock import (
    MockKeithleyDMM,
    MockMokuProDatalogger,
    MockOscilloscope,
    MockIT6412PowerSupply,
    MockUSB_RLY08C,
)
from camera_controller import CameraController
from data_logger import DataLogger
from api_models import (
    ControlSource,
    MeasurementConfig,
    MokuWaveformGeneratorStage,
    RelayStage,
    VoltageStage,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTRUMENT_ADDRESS_FILE = REPO_ROOT / "user-data" / "instrument-addresses.json"
DEFAULT_INSTRUMENT_VALUES = {"", "auto", "default"}
MAX_MOKU_WAVEFORM_VPP = 2.0


class MeasurementController:
    """
    Main controller for coordinating all instruments during a measurement.
    
    Manages:
    - DMM data acquisition
    - Power supply voltage stages
    - Relay switching stages
    - Camera recording
    - Data logging
    """

    def __init__(self, use_mock: bool = False):
        """
        Initialize measurement controller.
        
        Args:
            use_mock: If True, use mock instruments. If False, auto-detect.
        """
        # Auto-detect if we should use mock instruments
        if not use_mock:
            try:
                # Try to create real instruments and check for availability
                test_dmm = KeithleyDMM()
                available_devices = test_dmm.list_available_devices()
                usbtmc_devices = Oscilloscope.discover_usbtmc_devices()
                moku_devices = MokuProDatalogger.discover_devices(timeout_seconds=1.0)
                use_mock = (
                    len(available_devices) == 0
                    and len(usbtmc_devices) == 0
                    and len(moku_devices) == 0
                )
                if use_mock:
                    logger.warning("No VISA devices detected - using MOCK instruments")
                else:
                    logger.info(
                        "Found %s VISA devices, %s USBTMC devices, and %s Moku devices",
                        len(available_devices),
                        len(usbtmc_devices),
                        len(moku_devices),
                    )
            except Exception as e:
                logger.warning(f"Error detecting VISA devices: {e} - using MOCK instruments")
                use_mock = True
        
        # Initialize instruments based on mock mode
        if use_mock:
            logger.info("Initializing MOCK instruments for testing")
            self.dmm1 = MockKeithleyDMM(name="DMM1")
            self.dmm2 = MockKeithleyDMM(name="DMM2")
            self.oscilloscope = MockOscilloscope()
            self.moku = MockMokuProDatalogger()
            self.power_supply = MockIT6412PowerSupply()
            self.relay_board = MockUSB_RLY08C()
            self.use_mock = True
        else:
            logger.info("Initializing REAL instruments")
            self.dmm1 = KeithleyDMM()
            self.dmm2 = KeithleyDMM()
            self.oscilloscope = Oscilloscope()
            self.moku = MokuProDatalogger()
            self.power_supply = IT6412PowerSupply()
            self.relay_board = USB_RLY08C()
            self.use_mock = False
        
        self.camera = CameraController()
        self.data_logger = DataLogger()

        self.is_measuring = False
        self._is_stopping = False
        self.current_session_id: Optional[str] = None
        self.current_config: Optional[MeasurementConfig] = None
        self.control_source: Optional[ControlSource] = None
        self.runtime_events = deque(maxlen=200)
        self.start_time: Optional[float] = None
        self._start_monotonic: Optional[float] = None
        self.latest_reading = {
            "time": None,
            "dmm1_voltage": None,
            "dmm2_voltage": None,
            "sample_index": None,
            "read_duration_ms": None,
            "loop_duration_ms": None,
            "late_by_ms": None,
            "overrun": False,
        }
        self.acquisition_stats = {
            "requested_rate_hz": None,
            "measurement_source": None,
            "dmm_acquisition_mode": None,
            "sample_count": 0,
            "overrun_count": 0,
            "achieved_rate_hz": None,
            "last_read_duration_ms": None,
            "last_loop_duration_ms": None,
            "last_late_by_ms": None,
        }
        
        self._measurement_task: Optional[asyncio.Task] = None
        self._voltage_stage_task: Optional[asyncio.Task] = None
        self._moku_waveform_generator_task: Optional[asyncio.Task] = None
        self._moku_waveform_generator_stop_event: Optional[threading.Event] = None
        self._auto_stop_task: Optional[asyncio.Task] = None
        self._camera_start_task: Optional[asyncio.Task] = None
        self._camera_start_log_task: Optional[asyncio.Task] = None
        self._moku_stop_elapsed: Optional[float] = None
        self._oscilloscope_waveform_csv_path: Optional[str] = None
        self._oscilloscope_waveform_metadata_path: Optional[str] = None
        self._moku_waveform_csv_path: Optional[str] = None
        self._moku_waveform_metadata_path: Optional[str] = None
        self._record_camera_for_session = False
        self._relay_stage_tasks: list[asyncio.Task] = []
        self._relay_lock = asyncio.Lock()
        self._visa_details_cache: dict[str, dict] = {}

    async def start_measurement(
        self,
        config: MeasurementConfig,
        control_source: ControlSource = "api",
    ) -> str:
        """
        Start a measurement with the given configuration.

        Args:
            config: Measurement configuration

        Returns:
            Session ID

        Raises:
            RuntimeError: If measurement is already running or instruments fail to connect
        """
        if self.is_measuring or self._is_stopping:
            raise RuntimeError("Measurement already in progress")

        logger.info("Starting measurement...")
        config, address_resolution_events = self._resolve_instrument_addresses(config)
        self._validate_config(config)
        self.runtime_events.clear()
        self.current_config = config.model_copy(deep=True)
        self.control_source = control_source

        # Create new session
        self.current_session_id = self.data_logger.create_session(config.test_name)
        
        # Save configuration
        self.data_logger.save_config(config.model_dump())
        for event in address_resolution_events:
            self._record_event(event)
        self._record_event(
            f"Measurement requested by {control_source}; preparing hardware",
            source=control_source,
        )

        try:
            await self._connect_instruments(config)

            self._record_camera_for_session = False
            if config.record_camera:
                await self._prepare_camera_for_sync()
            else:
                self._record_event("Camera recording disabled")

            if config.measurement_source == "oscilloscope" and self.oscilloscope.is_connected:
                await asyncio.to_thread(self.oscilloscope.start_acquisition)
                self._record_event("Oscilloscope acquisition started")

            if config.measurement_source == "moku" and self.moku.is_connected:
                moku_duration = self._planned_high_rate_record_duration(config)
                if not moku_duration:
                    raise RuntimeError(
                        "Moku:Pro mode requires auto-stop or scheduled stages "
                        "so the logger duration is known"
                    )
                moku_response = await asyncio.to_thread(
                    self.moku.start_logging,
                    moku_duration,
                    config.moku_sample_rate_hz,
                    config.test_name,
                    "ECA app synchronized run",
                )
                self._record_event(
                    "Moku:Pro logging started before t0: "
                    f"target {moku_duration:g} s, "
                    "sample rate "
                    f"{moku_response.get('rate') or moku_response.get('sample_rate', config.moku_sample_rate_hz)} Sa/s, "
                    f"file {moku_response.get('file_name', 'unknown')}"
                )

            await self._apply_ready_delay(config.camera_ready_delay_seconds)
        except Exception as e:
            logger.error(f"Failed to start measurement: {e}")
            self._record_event(f"Failed to start measurement: {e}", kind="error")
            if self.camera.is_recording:
                await self.camera.stop_recording()
            self._disconnect_instruments()
            self.current_session_id = None
            self.current_config = None
            self.control_source = None
            self.start_time = None
            self._start_monotonic = None
            raise

        # Start data logging
        self.data_logger.start_logging()

        # Start measurement tasks
        self.is_measuring = True
        self.start_time = time.time()
        self._start_monotonic = time.perf_counter()
        self.latest_reading = {
            "time": 0.0,
            "dmm1_voltage": None,
            "dmm2_voltage": None,
            "sample_index": None,
            "read_duration_ms": None,
            "loop_duration_ms": None,
            "late_by_ms": None,
            "overrun": False,
        }
        self.acquisition_stats = {
            "requested_rate_hz": config.sampling_rate_hz,
            "measurement_source": config.measurement_source,
            "dmm_acquisition_mode": config.dmm_acquisition_mode,
            "sample_count": 0,
            "overrun_count": 0,
            "achieved_rate_hz": None,
            "last_read_duration_ms": None,
            "last_loop_duration_ms": None,
            "last_late_by_ms": None,
        }

        self._camera_start_task = None
        self._camera_start_log_task = None
        self._auto_stop_task = None
        self._oscilloscope_waveform_csv_path = None
        self._oscilloscope_waveform_metadata_path = None
        self._moku_waveform_csv_path = None
        self._moku_waveform_metadata_path = None
        self._moku_stop_elapsed = None
        self._record_event("Measurement t0")
        self._record_event(f"Measurement source: {config.measurement_source}")
        if config.measurement_source == "dmm":
            self._record_event(f"DMM acquisition mode: {config.dmm_acquisition_mode}")
        elif config.measurement_source == "oscilloscope" and self.oscilloscope.is_connected:
            self._record_event(
                "Oscilloscope CH1/CH2 full-record data will be exported to "
                "oscilloscope_waveform.csv at stop; readings.csv is timing-only "
                "in oscilloscope mode"
            )
        elif config.measurement_source == "moku" and self.moku.is_connected:
            self._record_moku_start_timing()
            self._record_event(
                "Moku:Pro CH1/CH2 logger data will be exported to "
                "moku_waveform.csv at stop; readings.csv is timing-only "
                "in Moku mode"
            )
            if config.moku_current_mode == "sr551_differential":
                self._record_event(
                    "Moku current mode: SR551 differential, "
                    "current_mA = (CH2 - CH3) / "
                    f"({config.current_amplifier_gain:g} * "
                    f"{config.current_shunt_ohms:g} ohm) * 1000; "
                    f"CH2/CH3 input range {config.moku_current_input_range}"
                )

        if config.record_camera:
            camera_start_requested_at = time.perf_counter()
            camera_start_request_offset_ms = (
                (camera_start_requested_at - self._start_monotonic) * 1000
                if self._start_monotonic is not None
                else 0.0
            )
            self._camera_start_task = asyncio.create_task(self.camera.start_recording())
            self._camera_start_log_task = asyncio.create_task(
                self._log_camera_start_result(
                    self._camera_start_task,
                    camera_start_request_offset_ms,
                )
            )

        # Start voltage acquisition task as close as possible to the camera command.
        self._measurement_task = asyncio.create_task(
            self._voltage_acquisition_loop(config)
        )

        # Start voltage stage task
        if config.voltage_stages:
            self._voltage_stage_task = asyncio.create_task(
                self._execute_voltage_stages(config.voltage_stages)
            )

        if config.moku_waveform_generator_stages and self.moku.is_connected:
            self._record_event(
                "Moku waveform generator schedule armed: "
                f"{len(config.moku_waveform_generator_stages)} stage(s) on Output 1"
            )
            self._moku_waveform_generator_stop_event = threading.Event()
            if self._start_monotonic is None:
                raise RuntimeError("Measurement clock is not running")
            self._moku_waveform_generator_task = asyncio.create_task(
                asyncio.to_thread(
                    self._run_moku_waveform_generator_schedule,
                    config.moku_waveform_generator_stages,
                    self._start_monotonic,
                    self._moku_waveform_generator_stop_event,
                )
            )

        # Start relay stage tasks
        if config.relay_ch1_stages:
            task = asyncio.create_task(
                self._execute_relay_stages(1, config.relay_ch1_stages)
            )
            self._relay_stage_tasks.append(task)

        if config.relay_ch2_stages:
            task = asyncio.create_task(
                self._execute_relay_stages(2, config.relay_ch2_stages)
            )
            self._relay_stage_tasks.append(task)

        if config.stop_after_seconds is not None:
            self._record_event(f"Auto stop armed for {config.stop_after_seconds:g} s")
            self._auto_stop_task = asyncio.create_task(
                self._auto_stop_at_elapsed_time(config.stop_after_seconds)
            )

        logger.info(f"Measurement started: {self.current_session_id}")
        return self.current_session_id

    def _record_event(
        self,
        message: str,
        kind: str = "info",
        source: Optional[ControlSource] = None,
        elapsed_time: Optional[float] = None,
        log: bool = True,
    ):
        """Record an operator/agent visible runtime event and mirror it to the session log."""
        if elapsed_time is None and self._start_monotonic is not None:
            elapsed_time = max(0.0, time.perf_counter() - self._start_monotonic)

        event = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "message": message,
            "kind": kind,
            "source": source or self.control_source,
            "elapsed_time": round(elapsed_time, 3) if elapsed_time is not None else None,
        }
        self.runtime_events.append(event)

        if log:
            self.data_logger.append_log(message)

    async def _prepare_camera_for_sync(self):
        """Prepare the camera before t0 without starting recording."""
        camera_prepared = await self.camera.prepare()
        if not camera_prepared:
            self._record_event("Camera requested but not prepared", kind="error")
            raise RuntimeError(
                "Camera recording was requested, but the camera could not be prepared. "
                "Power-cycle or replug the camera, then verify `camera/CameraControl detect`."
            )

        self._record_event("Camera prepared")
        self._record_camera_for_session = True

    async def _apply_ready_delay(self, ready_delay_seconds: float):
        """Wait after hardware prepare so t0 starts from a settled ready state."""
        if ready_delay_seconds > 0:
            self._record_event(f"Ready delay {ready_delay_seconds:.3f} s")
            await asyncio.sleep(ready_delay_seconds)

    async def _log_camera_start_result(
        self,
        camera_start_task: asyncio.Task,
        camera_start_request_offset_ms: float,
    ):
        """Log camera start acknowledgement without delaying relay/DMM/voltage t0."""
        camera_started = await camera_start_task
        camera_start_ack_ms = (
            (time.perf_counter() - self._start_monotonic) * 1000
            if self._start_monotonic is not None
            else 0.0
        )
        camera_command_ms = (
            self.camera.last_command_elapsed_us / 1000
            if self.camera.last_command_elapsed_us is not None
            else None
        )

        if not camera_started:
            self._record_event(
                "ERROR: Camera did not start; command requested "
                f"{camera_start_request_offset_ms:.3f} ms after measurement t0; "
                f"failed/acknowledged {camera_start_ack_ms:.3f} ms after measurement t0",
                kind="error",
            )
            return

        command_detail = (
            f"; camera command {camera_command_ms:.3f} ms"
            if camera_command_ms is not None
            else ""
        )
        timing_detail = self._camera_timing_offsets_detail()
        self._record_event(
            "Camera start command requested "
            f"{camera_start_request_offset_ms:.3f} ms after measurement t0; "
            f"acknowledged {camera_start_ack_ms:.3f} ms after measurement t0"
            f"{command_detail}{timing_detail}"
        )

    def _camera_timing_offsets_detail(self) -> str:
        """Format camera service/daemon timing fields relative to measurement t0."""
        timing = self.camera.last_timing
        if not timing or not self.start_time:
            return ""

        labels = (
            ("service received", "last_request_received_epoch_us"),
            ("daemon write", "last_command_write_epoch_us"),
            ("daemon received", "last_daemon_received_epoch_us"),
            ("EDSDK returned", "last_daemon_completed_epoch_us"),
            ("service responded", "last_response_epoch_us"),
        )
        parts = []
        for label, key in labels:
            offset = self._epoch_us_offset_ms(timing.get(key))
            if offset is not None:
                parts.append(f"{label} {offset:.3f} ms")

        http_elapsed = timing.get("last_http_elapsed_us")
        if isinstance(http_elapsed, (int, float)):
            parts.append(f"HTTP elapsed {http_elapsed / 1000:.3f} ms")

        return f"; {'; '.join(parts)}" if parts else ""

    def _epoch_us_offset_ms(self, epoch_us: object) -> Optional[float]:
        if not isinstance(epoch_us, (int, float)) or not self.start_time:
            return None
        return (float(epoch_us) / 1_000_000.0 - self.start_time) * 1000

    async def stop_measurement(self, control_source: ControlSource = "api") -> dict:
        """
        Stop the current measurement.

        Returns:
            Dictionary with session information and file paths
        """
        if self._is_stopping:
            # Idempotent no-op: a stop is already underway. Returning a benign
            # status (instead of raising) keeps repeated UI clicks and agent or
            # script retries from surfacing a spurious failure.
            logger.info("Stop requested while already stopping; treating as no-op")
            return {"status": "already_stopping", "session_id": self.current_session_id}
        if not self.is_measuring:
            logger.info("Stop requested with no active measurement; treating as no-op")
            return {"status": "not_measuring", "session_id": self.current_session_id}

        logger.info("Stopping measurement...")
        self._is_stopping = True
        self._record_event(f"Stop requested by {control_source}", source=control_source)
        stop_requested_perf = time.perf_counter()
        if self._moku_waveform_generator_stop_event:
            self._moku_waveform_generator_stop_event.set()
        oscilloscope_stop_elapsed = None
        oscilloscope_stop_task = None
        moku_stop_task = None
        moku_stop_elapsed = None
        camera_stop_task = None
        camera_stop_request_elapsed = None
        if (
            self.current_config
            and self.current_config.measurement_source == "oscilloscope"
            and self.oscilloscope.is_connected
            and self._start_monotonic is not None
        ):
            oscilloscope_stop_elapsed = max(0.0, stop_requested_perf - self._start_monotonic)
            oscilloscope_stop_task = asyncio.create_task(
                asyncio.to_thread(self.oscilloscope.stop_acquisition)
            )

        if (
            self.current_config
            and self.current_config.measurement_source == "moku"
            and self.moku.is_connected
            and self._start_monotonic is not None
        ):
            moku_stop_elapsed = max(0.0, stop_requested_perf - self._start_monotonic)
            self._moku_stop_elapsed = moku_stop_elapsed
            moku_stop_task = asyncio.create_task(
                asyncio.to_thread(self.moku.stop_logging)
            )

        if self._record_camera_for_session and self.camera.is_recording:
            camera_stop_request_elapsed = (
                max(0.0, time.perf_counter() - self._start_monotonic)
                if self._start_monotonic is not None
                else None
            )
            camera_stop_task = asyncio.create_task(self.camera.stop_recording())

        self.is_measuring = False
        if self._voltage_stage_task:
            self._voltage_stage_task.cancel()
        for task in self._relay_stage_tasks:
            task.cancel()

        if oscilloscope_stop_task or moku_stop_task or camera_stop_task:
            tasks = [
                task
                for task in (oscilloscope_stop_task, moku_stop_task, camera_stop_task)
                if task is not None
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            result_by_task = dict(zip(tasks, results))

            if oscilloscope_stop_task:
                result = result_by_task.get(oscilloscope_stop_task)
                if isinstance(result, Exception):
                    self._record_event(
                        f"Oscilloscope acquisition stop failed: {result}",
                        kind="error",
                        elapsed_time=oscilloscope_stop_elapsed,
                    )
                else:
                    self._record_event(
                        "Oscilloscope acquisition stopped",
                        elapsed_time=oscilloscope_stop_elapsed,
                    )

            if moku_stop_task:
                result = result_by_task.get(moku_stop_task)
                if isinstance(result, Exception):
                    self._record_event(
                        f"Moku:Pro logging stop failed: {result}",
                        kind="error",
                        elapsed_time=moku_stop_elapsed,
                    )
                else:
                    self._record_event(
                        "Moku:Pro logging stopped",
                        elapsed_time=moku_stop_elapsed,
                    )

            if camera_stop_task:
                result = result_by_task.get(camera_stop_task)
                camera_stop_ack_elapsed = (
                    max(0.0, time.perf_counter() - self._start_monotonic)
                    if self._start_monotonic is not None
                    else None
                )
                request_offset_ms = (
                    camera_stop_request_elapsed * 1000
                    if camera_stop_request_elapsed is not None
                    else None
                )
                ack_offset_ms = (
                    camera_stop_ack_elapsed * 1000
                    if camera_stop_ack_elapsed is not None
                    else None
                )
                if isinstance(result, Exception) or result is False:
                    detail = result if isinstance(result, Exception) else "command returned false"
                    self._record_event(
                        "Camera recording stop was not acknowledged; "
                        f"assuming camera already stopped and continuing: {detail}",
                        kind="warning",
                        elapsed_time=camera_stop_ack_elapsed,
                    )
                else:
                    request_text = (
                        f"{request_offset_ms:.3f} ms after measurement t0"
                        if request_offset_ms is not None
                        else "unknown offset"
                    )
                    ack_text = (
                        f"{ack_offset_ms:.3f} ms after measurement t0"
                        if ack_offset_ms is not None
                        else "unknown offset"
                    )
                    self._record_event(
                        "Camera recording stopped; stop command requested "
                        f"{request_text}; acknowledged {ack_text}"
                        f"{self._camera_timing_offsets_detail()}",
                        elapsed_time=camera_stop_ack_elapsed,
                    )

        elif self._record_camera_for_session:
            self._record_event(
                "Camera was requested but was not recording at stop",
                kind="warning",
            )

        current_task = asyncio.current_task()

        # Let the acquisition loop finish any in-flight instrument read before
        # disconnecting. Cancelling asyncio.to_thread does not stop the worker
        # thread, which can leave VISA USB resources claimed after Stop.
        if self._measurement_task:
            try:
                await asyncio.wait_for(self._measurement_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Voltage acquisition did not stop cleanly; cancelling task")
                self._measurement_task.cancel()
                try:
                    await self._measurement_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

        # Cancel all scheduled/control tasks
        if self._voltage_stage_task:
            self._voltage_stage_task.cancel()
            try:
                await self._voltage_stage_task
            except asyncio.CancelledError:
                pass

        if self._moku_waveform_generator_task:
            try:
                await asyncio.wait_for(self._moku_waveform_generator_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Moku waveform generator scheduler did not stop cleanly")
                self._moku_waveform_generator_task.cancel()
                try:
                    await self._moku_waveform_generator_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

        if self._auto_stop_task and self._auto_stop_task is not current_task:
            self._auto_stop_task.cancel()
            try:
                await self._auto_stop_task
            except asyncio.CancelledError:
                pass

        if self._camera_start_task and not self._camera_start_task.done():
            try:
                await self._camera_start_task
            except Exception:
                pass

        if self._camera_start_log_task and not self._camera_start_log_task.done():
            try:
                await self._camera_start_log_task
            except Exception:
                pass

        for task in self._relay_stage_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._relay_stage_tasks.clear()

        # Fallback if no synchronized camera stop request was issued above.
        if (
            self._record_camera_for_session
            and camera_stop_task is None
            and self.camera.is_recording
        ):
            stopped = await self.camera.stop_recording()
            if stopped:
                self._record_event("Camera recording stopped")
            else:
                self._record_event(
                    "Camera recording stop was not acknowledged; "
                    "assuming camera already stopped and continuing",
                    kind="warning",
                )
        elif self._record_camera_for_session and camera_stop_task is None:
            self._record_event("Camera was requested but was not recording at stop", kind="warning")

        # Stop data logging
        self.data_logger.stop_logging()

        if (
            self.current_config
            and self.current_config.measurement_source == "oscilloscope"
            and self.oscilloscope.is_connected
        ):
            await self._save_oscilloscope_waveform(oscilloscope_stop_elapsed)

        if (
            self.current_config
            and self.current_config.measurement_source == "moku"
            and self.moku.is_connected
        ):
            await self._save_moku_waveform(self._moku_stop_elapsed)

        # Disconnect instruments
        self._disconnect_instruments()

        # Prepare response
        session_dir = self.data_logger.current_session_dir
        response = {
            "status": "stopped",
            "session_id": self.current_session_id,
            "csv_path": str(self.data_logger.csv_file),
            "config_path": str(self.data_logger.config_file),
            "log_path": str(self.data_logger.log_file),
            "oscilloscope_csv_path": self._oscilloscope_waveform_csv_path,
            "oscilloscope_metadata_path": self._oscilloscope_waveform_metadata_path,
            "moku_csv_path": self._moku_waveform_csv_path,
            "moku_metadata_path": self._moku_waveform_metadata_path,
        }

        self._record_event("Measurement stopped", source=control_source)
        logger.info(f"Measurement stopped: {self.current_session_id}")

        self.current_session_id = None
        self.start_time = None
        self._start_monotonic = None
        self.control_source = None
        self._measurement_task = None
        self._voltage_stage_task = None
        self._moku_waveform_generator_task = None
        self._moku_waveform_generator_stop_event = None
        if self._auto_stop_task is not current_task:
            self._auto_stop_task = None
        self._is_stopping = False

        return response

    async def _save_oscilloscope_waveform(self, stop_elapsed_seconds: Optional[float]):
        """Export the stopped oscilloscope record into the current session."""
        try:
            waveform = await asyncio.to_thread(
                self.oscilloscope.capture_waveforms,
                stop_elapsed_seconds,
            )
            if not waveform or not self._has_oscilloscope_waveform_data(waveform):
                self._record_event("Oscilloscope waveform export produced no data", kind="warning")
                return

            csv_path, metadata_path = self.data_logger.save_oscilloscope_waveform(waveform)
            self._oscilloscope_waveform_csv_path = str(csv_path) if csv_path else None
            self._oscilloscope_waveform_metadata_path = (
                str(metadata_path) if metadata_path else None
            )
            if csv_path:
                self._record_event(f"Oscilloscope waveform saved to {csv_path.name}")
                self._record_oscilloscope_waveform_coverage(waveform)
        except Exception as e:
            logger.error("Failed to save oscilloscope waveform: %s", e)
            self._record_event(f"Oscilloscope waveform export failed: {e}", kind="error")

    async def _save_moku_waveform(self, stop_elapsed_seconds: Optional[float]):
        """Download and normalize the stopped Moku:Pro data logger record."""
        try:
            if not self.data_logger.current_session_dir:
                raise RuntimeError("No active session directory for Moku export")

            waveform = await asyncio.to_thread(
                self.moku.capture_waveforms,
                self.data_logger.current_session_dir,
                stop_elapsed_seconds,
                self._moku_t0_offset_seconds(),
            )
            if not waveform or not self._has_oscilloscope_waveform_data(waveform):
                self._record_event("Moku:Pro waveform export produced no data", kind="warning")
                return

            csv_path, metadata_path = self.data_logger.save_moku_waveform(waveform)
            self._moku_waveform_csv_path = str(csv_path) if csv_path else None
            self._moku_waveform_metadata_path = str(metadata_path) if metadata_path else None
            if csv_path:
                self._record_event(f"Moku:Pro waveform saved to {csv_path.name}")
                self._record_moku_waveform_coverage(waveform)
                self._record_moku_clipping(waveform.get("metadata"))
        except Exception as e:
            logger.error("Failed to save Moku:Pro waveform: %s", e)
            self._record_event(f"Moku:Pro waveform export failed: {e}", kind="error")

    @staticmethod
    def _has_oscilloscope_waveform_data(waveform: dict) -> bool:
        rows = waveform.get("rows")
        if rows:
            return True

        channels = waveform.get("channels", {})
        for channel in channels.values():
            if channel and channel.get("raw_values"):
                return True
        return False

    def _record_oscilloscope_waveform_coverage(self, waveform: dict) -> None:
        """Log whether the exported scope memory covers the full elapsed run."""
        rows = waveform.get("rows") or []
        metadata = waveform.get("metadata") or {}
        stop_elapsed_seconds = metadata.get("stop_elapsed_seconds")
        start_time = metadata.get("first_aligned_time")
        end_time = metadata.get("last_aligned_time")
        coverage_seconds = metadata.get("waveform_coverage_seconds")

        if (
            isinstance(start_time, (int, float))
            and isinstance(end_time, (int, float))
            and isinstance(coverage_seconds, (int, float))
        ):
            self._record_event(
                f"Oscilloscope waveform export covers {coverage_seconds:.6g} s "
                f"from t={start_time:.6g} s to t={end_time:.6g} s"
            )
        else:
            times = [
                row.get("time")
                for row in rows
                if isinstance(row.get("time"), (int, float))
            ]

            if not times:
                return

            start_time = min(times)
            end_time = max(times)
            coverage_seconds = max(0.0, end_time - start_time)
            self._record_event(
                f"Oscilloscope waveform export covers {coverage_seconds:.6g} s "
                f"from t={start_time:.6g} s to t={end_time:.6g} s"
            )
        if (
            isinstance(stop_elapsed_seconds, (int, float))
            and stop_elapsed_seconds > 0
            and start_time > 1.0
        ):
            self._record_event(
                "Oscilloscope waveform export is only the final scope memory window, "
                "not the full elapsed run; increase scope record span or use a "
                "separate lower-rate continuous logger for full-run CH1/CH2 voltage",
                kind="warning",
            )

    def _record_moku_waveform_coverage(self, waveform: dict) -> None:
        rows = waveform.get("rows") or []
        times = [
            row.get("time")
            for row in rows
            if isinstance(row.get("time"), (int, float))
        ]
        if not times:
            return

        start_time = min(times)
        end_time = max(times)
        coverage_seconds = max(0.0, end_time - start_time)
        self._record_event(
            f"Moku:Pro waveform export covers {coverage_seconds:.6g} s "
            f"from t={start_time:.6g} s to t={end_time:.6g} s"
        )

    def _record_moku_clipping(self, metadata: Optional[dict]) -> None:
        """Warn in the runtime log when a Moku input reached its frontend rail."""
        clipping = (metadata or {}).get("clipping") or {}
        if not clipping.get("any_clipped"):
            return
        parts = []
        for channel, info in (clipping.get("channels") or {}).items():
            if info.get("clipped"):
                parts.append(
                    f"{channel} {info['clipped_samples']} sample(s) "
                    f"({info['clipped_fraction'] * 100:.2f}%, peak "
                    f"{info['max_abs_input_volts']:.4g} V vs {info['rail_volts']:g} V rail)"
                )
        if parts:
            self._record_event(
                "Moku input clipping at the frontend rail: "
                + "; ".join(parts)
                + ". Reduce amplifier gain or drive amplitude.",
                kind="warning",
            )

    def _record_moku_start_timing(self) -> None:
        request_time = getattr(self.moku, "last_logging_start_request_monotonic", None)
        ack_time = getattr(self.moku, "last_logging_start_ack_monotonic", None)
        if self._start_monotonic is None or request_time is None:
            return

        request_before_t0_ms = (self._start_monotonic - request_time) * 1000
        ack_before_t0_ms = (
            (self._start_monotonic - ack_time) * 1000
            if ack_time is not None
            else None
        )
        ack_text = (
            f"; acknowledged {ack_before_t0_ms:.3f} ms before t0"
            if ack_before_t0_ms is not None
            else ""
        )
        self._record_event(
            "Moku:Pro logging command requested "
            f"{request_before_t0_ms:.3f} ms before measurement t0{ack_text}"
        )

    def _moku_t0_offset_seconds(self) -> Optional[float]:
        ack_time = getattr(self.moku, "last_logging_start_ack_monotonic", None)
        request_time = getattr(self.moku, "last_logging_start_request_monotonic", None)
        if self._start_monotonic is None:
            return None

        reference_time = ack_time if ack_time is not None else request_time
        if reference_time is None:
            return None
        return max(0.0, self._start_monotonic - reference_time)

    def _resolve_instrument_addresses(
        self,
        config: MeasurementConfig,
    ) -> tuple[MeasurementConfig, list[str]]:
        """Resolve default/auto instrument addresses from the central address file."""
        address_book = self._load_instrument_address_book()
        resolved = config.model_copy(deep=True)
        events: list[str] = []

        fields = (
            "dmm1_visa_id",
            "dmm2_visa_id",
            "oscilloscope_visa_id",
            "moku_address",
            "power_supply_visa_id",
            "relay_port",
        )
        inventory: Optional[dict] = None

        for field in fields:
            current_value = getattr(resolved, field)
            if not self._uses_default_instrument_value(current_value):
                continue

            requested = address_book.get(field)
            if self._uses_default_instrument_value(requested):
                requested = "auto"

            if inventory is None:
                inventory = self.list_available_instruments()

            value = self._resolve_single_instrument_address(
                field,
                requested,
                address_book,
                inventory,
            )
            if value:
                setattr(resolved, field, value)
                events.append(f"Resolved {field} from instrument-addresses.json: {value}")
            else:
                setattr(resolved, field, None)

        return resolved, events

    @staticmethod
    def _uses_default_instrument_value(value: object) -> bool:
        if value is None:
            return True
        return isinstance(value, str) and value.strip().lower() in DEFAULT_INSTRUMENT_VALUES

    @staticmethod
    def _load_instrument_address_book() -> dict:
        if not INSTRUMENT_ADDRESS_FILE.exists():
            return {}
        try:
            data = json.loads(INSTRUMENT_ADDRESS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", INSTRUMENT_ADDRESS_FILE, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _resolve_single_instrument_address(
        self,
        field: str,
        requested: object,
        address_book: dict,
        inventory: dict,
    ) -> Optional[str]:
        if isinstance(requested, str) and requested.strip().lower() not in DEFAULT_INSTRUMENT_VALUES:
            return requested.strip()

        if field == "moku_address":
            return self._resolve_moku_address(address_book, inventory)
        if field == "relay_port":
            return self._only_or_none(inventory.get("serial_ports") or [])
        if field == "oscilloscope_visa_id":
            return self._only_or_none(inventory.get("oscilloscope_resources") or [])
        if field == "power_supply_visa_id":
            return self._only_or_none(inventory.get("power_supply_resources") or [])
        if field in {"dmm1_visa_id", "dmm2_visa_id"}:
            dmm_resources = inventory.get("dmm_resources") or []
            index = 0 if field == "dmm1_visa_id" else 1
            if len(dmm_resources) > index:
                return dmm_resources[index]
        return None

    @staticmethod
    def _resolve_moku_address(address_book: dict, inventory: dict) -> Optional[str]:
        serial = str(address_book.get("moku_serial") or "").strip()
        moku_details = [
            item
            for item in inventory.get("visa_details", [])
            if item.get("kind") == "moku"
        ]
        if serial:
            for item in moku_details:
                idn = str(item.get("idn") or "")
                if f"serial {serial}" in idn or f"serial, {serial}" in idn or serial in idn:
                    return item.get("resource")

        resources = inventory.get("moku_resources") or []
        return MeasurementController._only_or_none(resources)

    @staticmethod
    def _only_or_none(values: list) -> Optional[str]:
        return str(values[0]) if len(values) == 1 and values[0] else None

    async def _auto_stop_at_elapsed_time(self, stop_after_seconds: float):
        """Stop the run when the configured elapsed time is reached."""
        try:
            while self.is_measuring:
                if self._start_monotonic is None:
                    return

                remaining = self._start_monotonic + stop_after_seconds - time.perf_counter()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 1.0))

            if self.is_measuring:
                self._record_event(f"Auto stop reached {stop_after_seconds:g} s")
                await self.stop_measurement(control_source="script")
        except asyncio.CancelledError:
            raise
        finally:
            if self._auto_stop_task is asyncio.current_task():
                self._auto_stop_task = None

    def _validate_config(self, config: MeasurementConfig):
        """Fail early when the UI submitted an incomplete hardware schedule."""
        if config.voltage_stages and not config.power_supply_visa_id:
            raise RuntimeError("Power supply stages require a selected IT6412 VISA ID")

        if (config.relay_ch1_stages or config.relay_ch2_stages) and not config.relay_port:
            raise RuntimeError("Relay stages require a selected relay board serial port")

        if (
            config.measurement_source == "dmm"
            and config.dmm1_visa_id
            and config.dmm2_visa_id
            and config.dmm1_visa_id == config.dmm2_visa_id
        ):
            raise RuntimeError("DMM1 and DMM2 must use different VISA IDs")

        if (
            config.measurement_source == "oscilloscope"
            and not self.use_mock
            and not config.oscilloscope_visa_id
        ):
            raise RuntimeError("Oscilloscope mode requires a selected oscilloscope VISA ID")

        if (
            config.measurement_source == "moku"
            and not self.use_mock
            and not config.moku_address
        ):
            raise RuntimeError("Moku:Pro mode requires a selected Moku address")

        if config.measurement_source == "moku" and config.moku_sample_rate_hz < 10:
            raise RuntimeError("Moku:Pro sample rate must be at least 10 Hz")

        if config.moku_waveform_generator_stages and config.measurement_source != "moku":
            raise RuntimeError("Moku waveform generator stages require Moku:Pro mode")

        if (
            config.measurement_source == "dmm"
            and config.dmm_acquisition_mode == "low_noise"
            and config.sampling_rate_hz > 20
        ):
            logger.warning(
                "Low-noise DMM mode uses 1 PLC integration and may not sustain %.1f Hz",
                config.sampling_rate_hz,
            )

        for index, stage in enumerate(config.voltage_stages, start=1):
            if stage.end_time <= stage.start_time:
                raise RuntimeError(f"Power stage {index} end time must be after start time")

        for index, stage in enumerate(config.moku_waveform_generator_stages, start=1):
            if stage.end_time <= stage.start_time:
                raise RuntimeError(
                    f"Moku waveform generator stage {index} end time must be after start time"
                )
            if stage.vpp > MAX_MOKU_WAVEFORM_VPP:
                raise RuntimeError(
                    f"Moku waveform generator stage {index} exceeds "
                    f"{MAX_MOKU_WAVEFORM_VPP:g} Vpp safety limit"
                )

        for channel, stages in ((1, config.relay_ch1_stages), (2, config.relay_ch2_stages)):
            for index, stage in enumerate(stages, start=1):
                if stage.end_time <= stage.start_time:
                    raise RuntimeError(
                        f"Relay CH{channel} stage {index} end time must be after start time"
                    )

    async def _connect_instruments(self, config: MeasurementConfig):
        """Connect all configured instruments."""
        # In mock mode, connect automatically if no address specified
        if self.use_mock:
            if config.measurement_source == "oscilloscope":
                self.oscilloscope.connect(
                    config.oscilloscope_visa_id or "MOCK::SCOPE::OSC1::INSTR"
                )
                self.oscilloscope.configure_voltage_channels()
                logger.info("Mock Oscilloscope connected")
            elif config.measurement_source == "moku":
                self.moku.connect(
                    config.moku_address or "MOKU::MOCK::PRO",
                    use_multi_instrument=bool(config.moku_waveform_generator_stages),
                )
                self.moku.configure_voltage_channels(
                    config.moku_sample_rate_hz,
                    current_mode=config.moku_current_mode,
                    shunt_ohms=config.current_shunt_ohms,
                    amplifier_gain=config.current_amplifier_gain,
                    current_input_range=config.moku_current_input_range,
                )
                logger.info("Mock Moku:Pro connected")
            else:
                self.dmm1.connect(config.dmm1_visa_id or "MOCK::DMM::DMM1::INSTR")
                self.dmm1.configure_acquisition_mode(config.dmm_acquisition_mode)
                logger.info("Mock DMM1 connected")

                self.dmm2.connect(config.dmm2_visa_id or "MOCK::DMM::DMM2::INSTR")
                self.dmm2.configure_acquisition_mode(config.dmm_acquisition_mode)
                logger.info("Mock DMM2 connected")
            
            self.power_supply.connect(config.power_supply_visa_id or "MOCK::POWER::IT6412::INSTR")
            logger.info("Mock Power Supply connected")
            
            self.relay_board.connect(config.relay_port or "MOCK_COM3")
            logger.info("Mock Relay Board connected")
        else:
            if config.measurement_source == "oscilloscope":
                success = self.oscilloscope.connect(config.oscilloscope_visa_id)
                if not success:
                    error_detail = (
                        f": {self.oscilloscope.last_error}"
                        if getattr(self.oscilloscope, "last_error", None)
                        else ""
                    )
                    raise RuntimeError(
                        f"Failed to connect oscilloscope: {config.oscilloscope_visa_id}"
                        f"{error_detail}"
                )
                logger.info(f"Oscilloscope connected: {config.oscilloscope_visa_id}")
                full_record_duration = self._planned_high_rate_record_duration(config)
                if full_record_duration:
                    settings = await asyncio.to_thread(
                        self.oscilloscope.configure_full_record,
                        full_record_duration,
                    )
                    self._record_event(
                        "Oscilloscope full-record capture configured: "
                        f"target {full_record_duration:g} s, "
                        f"scale {settings.get('actual_scale_seconds_per_div')} s/div, "
                        f"record length {settings.get('actual_record_length')}, "
                        f"sample rate {settings.get('actual_sample_rate_hz')} Sa/s"
                    )
            elif config.measurement_source == "moku":
                success = self.moku.connect(
                    config.moku_address,
                    use_multi_instrument=bool(config.moku_waveform_generator_stages),
                )
                if not success:
                    error_detail = (
                        f": {self.moku.last_error}"
                        if getattr(self.moku, "last_error", None)
                        else ""
                    )
                    raise RuntimeError(
                        f"Failed to connect Moku:Pro: {config.moku_address}{error_detail}"
                    )
                await asyncio.to_thread(
                    self.moku.configure_voltage_channels,
                    config.moku_sample_rate_hz,
                    current_mode=config.moku_current_mode,
                    shunt_ohms=config.current_shunt_ohms,
                    amplifier_gain=config.current_amplifier_gain,
                    current_input_range=config.moku_current_input_range,
                )
                logger.info("Moku:Pro connected: %s", config.moku_address)
            else:
                # Connect real DMMs only if VISA ID provided
                if config.dmm1_visa_id:
                    success = self.dmm1.connect(config.dmm1_visa_id)
                    if not success:
                        raise RuntimeError(f"Failed to connect DMM1: {config.dmm1_visa_id}")
                    self.dmm1.configure_acquisition_mode(config.dmm_acquisition_mode)
                    logger.info(f"DMM1 connected: {config.dmm1_visa_id}")

                if config.dmm2_visa_id:
                    success = self.dmm2.connect(config.dmm2_visa_id)
                    if not success:
                        raise RuntimeError(f"Failed to connect DMM2: {config.dmm2_visa_id}")
                    self.dmm2.configure_acquisition_mode(config.dmm_acquisition_mode)
                    logger.info(f"DMM2 connected: {config.dmm2_visa_id}")

            if config.power_supply_visa_id:
                success = self.power_supply.connect(config.power_supply_visa_id)
                if not success:
                    raise RuntimeError(f"Failed to connect power supply: {config.power_supply_visa_id}")
                logger.info(f"Power supply connected: {config.power_supply_visa_id}")

            if config.relay_port:
                success = self.relay_board.connect(config.relay_port)
                if not success:
                    raise RuntimeError(f"Failed to connect relay board: {config.relay_port}")
                logger.info(f"Relay board connected: {config.relay_port}")

            await self._prime_voltage_reads(config.measurement_source)

    @staticmethod
    def _planned_high_rate_record_duration(config: MeasurementConfig) -> Optional[float]:
        """Return the elapsed time a high-rate source should retain/log."""
        candidates = []
        if config.stop_after_seconds is not None:
            candidates.append(config.stop_after_seconds)

        candidates.extend(stage.end_time for stage in config.voltage_stages)
        candidates.extend(stage.end_time for stage in config.relay_ch1_stages)
        candidates.extend(stage.end_time for stage in config.relay_ch2_stages)
        candidates.extend(stage.end_time for stage in config.moku_waveform_generator_stages)

        duration = max(candidates, default=0.0)
        ready_delay = max(0.0, config.camera_ready_delay_seconds)
        return duration + ready_delay if duration > 0 else None

    async def _prime_voltage_reads(self, measurement_source: str):
        """Perform one unlogged read so setup latency does not hit sample 0."""
        if measurement_source in {"oscilloscope", "moku"}:
            # In high-rate modes the instrument records its own waveform. The
            # live readings.csv stream is timing/status only.
            return

        prime_tasks = []
        if self.dmm1.is_connected:
            prime_tasks.append(asyncio.to_thread(self.dmm1.read_voltage))
        if self.dmm2.is_connected:
            prime_tasks.append(asyncio.to_thread(self.dmm2.read_voltage))

        if prime_tasks:
            await asyncio.gather(*prime_tasks)

    def _disconnect_instruments(self):
        """Disconnect all instruments."""
        self.dmm1.disconnect()
        self.dmm2.disconnect()
        self.oscilloscope.disconnect()
        self.moku.disconnect()
        self.power_supply.disconnect()
        self.relay_board.disconnect()
        logger.info("All instruments disconnected")

    async def _voltage_acquisition_loop(self, config: MeasurementConfig):
        """
        Continuous voltage acquisition loop.

        Args:
            config: Measurement configuration for source and sampling rate.
        """
        interval = 1.0 / config.sampling_rate_hz
        next_sample_at = time.perf_counter()
        sample_index = 0
        logger.info(
            "%s voltage acquisition started at %.3f Hz",
            config.measurement_source,
            config.sampling_rate_hz,
        )

        try:
            while self.is_measuring:
                now = time.perf_counter()
                if now < next_sample_at:
                    await asyncio.sleep(next_sample_at - now)
                if not self.is_measuring:
                    break

                loop_start = time.perf_counter()
                late_by_ms = max(0.0, (loop_start - next_sample_at) * 1000)

                # Get current elapsed time
                elapsed = loop_start - self._start_monotonic

                read_start = time.perf_counter()
                if config.measurement_source in {"oscilloscope", "moku"}:
                    # High-rate instruments export their CH1/CH2 records at stop.
                    dmm1_voltage, dmm2_voltage = None, None
                else:
                    read_tasks = []
                    if self.dmm1.is_connected:
                        read_tasks.append(asyncio.to_thread(self.dmm1.read_voltage))
                    else:
                        read_tasks.append(self._none_async())

                    if self.dmm2.is_connected:
                        read_tasks.append(asyncio.to_thread(self.dmm2.read_voltage))
                    else:
                        read_tasks.append(self._none_async())

                    dmm1_voltage, dmm2_voltage = await asyncio.gather(*read_tasks)
                read_duration_ms = (time.perf_counter() - read_start) * 1000
                loop_duration_ms = (time.perf_counter() - loop_start) * 1000
                overrun = loop_duration_ms > interval * 1000

                # Log the reading
                self.data_logger.log_reading(
                    elapsed,
                    dmm1_voltage,
                    dmm2_voltage,
                    sample_index,
                    read_duration_ms,
                    loop_duration_ms,
                    late_by_ms,
                    overrun,
                )

                sample_count = sample_index + 1
                if overrun:
                    self.acquisition_stats["overrun_count"] += 1

                runtime = max(0.001, time.perf_counter() - self._start_monotonic)
                self.acquisition_stats.update({
                    "sample_count": sample_count,
                    "achieved_rate_hz": sample_count / runtime,
                    "last_read_duration_ms": read_duration_ms,
                    "last_loop_duration_ms": loop_duration_ms,
                    "last_late_by_ms": late_by_ms,
                })
                self.latest_reading = {
                    "time": elapsed,
                    "dmm1_voltage": dmm1_voltage,
                    "dmm2_voltage": dmm2_voltage,
                    "sample_index": sample_index,
                    "read_duration_ms": read_duration_ms,
                    "loop_duration_ms": loop_duration_ms,
                    "late_by_ms": late_by_ms,
                    "overrun": overrun,
                }

                if overrun and sample_count % 10 == 0:
                    logger.warning(
                        "%s acquisition overrun: requested %.3f ms, last loop %.3f ms",
                        config.measurement_source,
                        interval * 1000,
                        loop_duration_ms,
                    )

                # Sleep for the remainder of the interval
                sample_index += 1
                next_sample_at += interval
                if next_sample_at < time.perf_counter():
                    next_sample_at = time.perf_counter()

        except asyncio.CancelledError:
            logger.info("%s voltage acquisition stopped", config.measurement_source)
            raise

    async def _none_async(self):
        """Async placeholder for disconnected DMM channels."""
        return None

    async def _execute_voltage_stages(self, stages: list[VoltageStage]):
        """
        Execute voltage stages according to schedule.

        Args:
            stages: List of voltage stages
        """
        if not self.power_supply.is_connected:
            logger.warning("Power supply not connected, skipping voltage stages")
            return

        logger.info(f"Executing {len(stages)} voltage stages")
        await asyncio.to_thread(self.power_supply.set_output_on)

        try:
            for i, stage in enumerate(stages):
                # Wait until stage start time
                while True:
                    elapsed = time.perf_counter() - self._start_monotonic
                    if elapsed >= stage.start_time:
                        break
                    await asyncio.sleep(0.01)

                # Set voltage
                await asyncio.to_thread(self.power_supply.set_voltage, stage.voltage)
                logger.info(f"Voltage stage {i+1}: {stage.voltage}V at {elapsed:.2f}s")
                self._record_event(f"Voltage set to {stage.voltage}V")

                # Wait until stage end time
                while True:
                    elapsed = time.perf_counter() - self._start_monotonic
                    if elapsed >= stage.end_time:
                        break
                    await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info("Voltage stages cancelled")
            raise
        finally:
            # Safe shutdown
            await asyncio.to_thread(self.power_supply.set_voltage, 0.0)
            await asyncio.to_thread(self.power_supply.set_output_off)
            logger.info("Voltage stages completed")

    def _run_moku_waveform_generator_schedule(
        self,
        stages: list[MokuWaveformGeneratorStage],
        start_monotonic: float,
        stop_event: threading.Event,
    ):
        """Execute Moku:Pro waveform-generator stages in a dedicated worker thread."""
        if not self.moku.is_connected:
            logger.warning("Moku:Pro not connected, skipping waveform generator stages")
            self._record_event(
                "Moku waveform generator stages skipped because Moku:Pro is not connected",
                kind="warning",
            )
            return

        logger.info("Executing %s Moku waveform generator stages", len(stages))
        self._record_event(
            "Moku waveform generator task started; waiting for first stage"
        )

        try:
            for i, stage in enumerate(stages):
                stage_number = i + 1
                current_elapsed = max(0.0, time.perf_counter() - start_monotonic)
                self._record_event(
                    "Moku waveform generator stage "
                    f"{stage_number} queued for {stage.start_time:g}-{stage.end_time:g} s"
                    f"; current elapsed {current_elapsed:.3f} s"
                )
                if self._wait_for_moku_stage_time(
                    stage.start_time,
                    start_monotonic,
                    stop_event,
                ):
                    return

                elapsed = time.perf_counter() - start_monotonic

                self.moku.generate_waveform(
                    stage.waveform,
                    stage.vpp,
                    stage.frequency_hz,
                )
                logger.info(
                    "Moku waveform generator stage %s: %s %.6g Vpp %.6g Hz at %.2fs",
                    stage_number,
                    stage.waveform,
                    stage.vpp,
                    stage.frequency_hz,
                    elapsed,
                )
                self._record_event(
                    "Moku waveform generator "
                    f"{stage.waveform}, {stage.vpp:g} Vpp, {stage.frequency_hz:g} Hz "
                    f"on Output 1 at {elapsed:.3f} s"
                )

                if self._wait_for_moku_stage_time(
                    stage.end_time,
                    start_monotonic,
                    stop_event,
                ):
                    return

                next_stage = stages[i + 1] if i + 1 < len(stages) else None
                has_gap_before_next_stage = (
                    next_stage is not None and next_stage.start_time > stage.end_time
                )
                if has_gap_before_next_stage:
                    self.moku.stop_waveform_generator()
                    self._record_event("Moku waveform generator set to 0 V")

        except Exception as exc:
            logger.error("Moku waveform generator stage execution failed: %s", exc)
            self._record_event(
                f"Moku waveform generator stage execution failed: {exc}",
                kind="error",
            )
        finally:
            try:
                self.moku.stop_waveform_generator()
                self._record_event("Moku waveform generator set to 0 V")
            except Exception as exc:
                logger.warning("Failed to stop Moku waveform generator: %s", exc)
                self._record_event(
                    f"Moku waveform generator shutdown failed: {exc}",
                    kind="warning",
                )
            logger.info("Moku waveform generator stages completed")

    def _wait_for_moku_stage_time(
        self,
        elapsed_seconds: float,
        start_monotonic: float,
        stop_event: threading.Event,
    ) -> bool:
        """Return True when the scheduler should stop before the target time."""
        current_elapsed = time.perf_counter() - start_monotonic
        remaining = elapsed_seconds - current_elapsed
        if not math.isfinite(remaining):
            raise RuntimeError(f"Measurement clock produced non-finite remaining time: {remaining}")
        if remaining <= 0:
            return stop_event.is_set()
        logger.debug(
            "Waiting %.3f s for Moku waveform generator elapsed %.3f s",
            remaining,
            elapsed_seconds,
        )
        return stop_event.wait(remaining)

    async def _execute_relay_stages(self, channel: int, stages: list[RelayStage]):
        """
        Execute relay stages according to schedule.

        Args:
            channel: Relay channel (1 or 2)
            stages: List of relay stages
        """
        if not self.relay_board.is_connected:
            logger.warning("Relay board not connected, skipping relay stages")
            return

        logger.info(f"Executing {len(stages)} relay stages for channel {channel}")

        try:
            initial_success = await self._set_relay_state(channel, False)
            if not initial_success:
                raise RuntimeError(f"Failed to set relay CH{channel} to open")

            for i, stage in enumerate(stages):
                # Wait until stage start time
                while True:
                    elapsed = time.perf_counter() - self._start_monotonic
                    if elapsed >= stage.start_time:
                        break
                    await asyncio.sleep(0.01)

                # Set relay state
                success = await self._set_relay_state(channel, stage.state == "closed")
                if not success:
                    raise RuntimeError(f"Failed to set relay CH{channel} to {stage.state}")

                logger.info(f"Relay CH{channel} stage {i+1}: {stage.state} at {elapsed:.2f}s")
                self._record_event(f"Relay CH{channel} set to {stage.state}")

                # Wait until stage end time
                while True:
                    elapsed = time.perf_counter() - self._start_monotonic
                    if elapsed >= stage.end_time:
                        break
                    await asyncio.sleep(0.01)

                next_stage = stages[i + 1] if i + 1 < len(stages) else None
                has_gap_before_next_stage = (
                    next_stage is not None and next_stage.start_time > stage.end_time
                )
                if stage.state == "closed" and has_gap_before_next_stage:
                    success = await self._set_relay_state(channel, False)
                    if not success:
                        raise RuntimeError(f"Failed to set relay CH{channel} to open")
                    logger.info(f"Relay CH{channel}: open at {elapsed:.2f}s")
                    self._record_event(f"Relay CH{channel} set to open")

        except asyncio.CancelledError:
            logger.info(f"Relay CH{channel} stages cancelled")
            raise
        finally:
            # Safe shutdown - open relay
            await self._set_relay_state(channel, False)
            logger.info(f"Relay CH{channel} stages completed")

    async def _set_relay_state(self, channel: int, state: bool) -> bool:
        """Serialize relay board writes across channel tasks."""
        async with self._relay_lock:
            if state:
                return await asyncio.to_thread(self.relay_board.set_relay_on, channel)
            return await asyncio.to_thread(self.relay_board.set_relay_off, channel)

    def get_status(self) -> dict:
        """
        Get current system status.

        Returns:
            Dictionary with status information
        """
        elapsed = None
        status_is_measuring = self.is_measuring or self._is_stopping
        if status_is_measuring and self.start_time:
            elapsed = round(time.time() - self.start_time, 1)

        return {
            "is_measuring": status_is_measuring,
            "is_stopping": self._is_stopping,
            "camera_recording": self.camera.is_recording,
            "camera_available": self.camera.is_available,
            "camera_timing": self.camera.last_timing,
            "session_id": self.current_session_id,
            "elapsed_time": elapsed,
            "mock_mode": self.use_mock,
            "acquisition": self.acquisition_stats,
            "active_config": self.current_config.model_dump() if self.current_config else None,
            "control_source": self.control_source,
            "events": list(self.runtime_events),
            "instruments": [
                {
                    "name": "DMM1" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.dmm1.is_connected,
                    "address": self.dmm1.visa_address
                },
                {
                    "name": "DMM2" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.dmm2.is_connected,
                    "address": self.dmm2.visa_address
                },
                {
                    "name": "Oscilloscope" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.oscilloscope.is_connected,
                    "address": self.oscilloscope.visa_address
                },
                {
                    "name": "Moku:Pro" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.moku.is_connected,
                    "address": self.moku.moku_address
                },
                {
                    "name": "Power Supply" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.power_supply.is_connected,
                    "address": self.power_supply.visa_address
                },
                {
                    "name": "Relay Board" + (" (MOCK)" if self.use_mock else ""),
                    "connected": self.relay_board.is_connected,
                    "address": self.relay_board.port
                }
            ]
        }

    def list_available_instruments(self) -> dict:
        """
        List all available instruments.

        Returns:
            Dictionary with lists of VISA resources and serial ports
        """
        if self.use_mock:
            visa_resources = self.dmm1.list_available_devices()
            serial_ports = MockUSB_RLY08C.list_available_ports()
            moku_devices = self.moku.discover_devices()
            visa_details = [
                {
                    "resource": resource,
                    "idn": resource,
                    "kind": (
                        "power_supply"
                        if "POWER" in resource
                        else "oscilloscope"
                        if "SCOPE" in resource
                        else "dmm"
                    ),
                    "label": resource,
                }
                for resource in visa_resources
            ]
            for device in moku_devices:
                visa_details.append(
                    {
                        "resource": device["resource"],
                        "idn": device.get("idn"),
                        "kind": "moku",
                        "label": device.get("label", device["resource"]),
                    }
                )
        else:
            visa_resources = self.dmm1.list_available_devices()
            serial_ports = USB_RLY08C.list_available_ports()
            if self.is_measuring:
                visa_details = [
                    self._visa_details_cache.get(resource) or self._infer_visa_resource(resource)
                    for resource in visa_resources
                ]
            else:
                visa_details = [self._identify_visa_resource(resource) for resource in visa_resources]

            known_resources = set(visa_resources)
            for device in Oscilloscope.discover_usbtmc_devices():
                resource = str(device["resource"])
                if resource in known_resources:
                    continue

                known_resources.add(resource)
                visa_resources.append(resource)
                detail = self._infer_visa_resource(resource, str(device.get("idn") or ""))
                detail["idn"] = device.get("idn")
                detail["label"] = self._format_usbtmc_label(device)
                visa_details.append(detail)

            for device in MokuProDatalogger.discover_devices(timeout_seconds=1.0):
                resource = str(device["resource"])
                if resource in known_resources:
                    continue

                known_resources.add(resource)
                visa_resources.append(resource)
                visa_details.append(
                    {
                        "resource": resource,
                        "idn": device.get("idn"),
                        "kind": "moku",
                        "label": device.get("label", resource),
                    }
                )

        dmm_resources = [item["resource"] for item in visa_details if item["kind"] == "dmm"]
        oscilloscope_resources = [
            item["resource"] for item in visa_details if item["kind"] == "oscilloscope"
        ]
        power_supply_resources = [
            item["resource"] for item in visa_details if item["kind"] == "power_supply"
        ]
        moku_resources = [item["resource"] for item in visa_details if item["kind"] == "moku"]
        unknown_resources = [
            item["resource"] for item in visa_details if item["kind"] == "unknown"
        ]

        return {
            "visa_resources": visa_resources,
            "dmm_resources": dmm_resources + unknown_resources,
            "oscilloscope_resources": oscilloscope_resources + unknown_resources,
            "moku_resources": moku_resources,
            "power_supply_resources": power_supply_resources + unknown_resources,
            "visa_details": visa_details,
            "serial_ports": serial_ports
        }

    def _format_usbtmc_label(self, device: dict) -> str:
        """Format a detected USBTMC device for the instrument selector."""
        manufacturer = device.get("manufacturer")
        product = device.get("product")
        serial = device.get("serial")
        device_path = device.get("device_path")
        name = " ".join(str(part) for part in (manufacturer, product) if part)
        serial_text = f" {serial}" if serial else ""
        suffix = f" ({device_path})" if device_path else ""
        return f"Scope: {name or device.get('idn') or device.get('resource')}{serial_text}{suffix}"

    def _infer_visa_resource(self, resource: str, idn: Optional[str] = None) -> dict:
        """Classify a VISA resource from cached IDN data or known USB IDs."""
        kind = "unknown"

        upper_idn = (idn or "").upper()
        upper_resource = resource.upper()

        if "KEITHLEY" in upper_idn or "2110" in upper_idn:
            kind = "dmm"
        elif "IT6412" in upper_idn or "ITECH" in upper_idn or "IT-M" in upper_idn:
            kind = "power_supply"
        elif any(
            marker in upper_idn
            for marker in (
                "OSCILLOSCOPE",
                "TEKTRONIX",
                "TBS",
                "RIGOL",
                "SIGLENT",
                "LECROY",
                "MSO",
                "DSO",
            )
        ):
            kind = "oscilloscope"
        elif "MOKU" in upper_idn or upper_resource.startswith("MOKU::"):
            kind = "moku"
        elif "11975::25618" in upper_resource:
            kind = "power_supply"
        elif "1510::8464" in upper_resource:
            kind = "dmm"
        elif (
            "SCOPE" in upper_resource
            or "OSC" in upper_resource
            or "USBTMC::" in upper_resource
            or "0699::03C7" in upper_resource
            or "1689::967" in upper_resource
        ):
            kind = "oscilloscope"

        label_prefix = {
            "dmm": "DMM",
            "moku": "Moku",
            "oscilloscope": "Scope",
            "power_supply": "Power",
        }.get(kind, "VISA")
        label = f"{label_prefix}: {idn or resource}"

        return {
            "resource": resource,
            "idn": idn,
            "kind": kind,
            "label": label,
        }

    def _identify_visa_resource(self, resource: str) -> dict:
        """Query *IDN? and classify VISA resources for safer UI selection."""
        idn = None

        try:
            rm = pyvisa.ResourceManager()
            instrument = rm.open_resource(resource)
            instrument.timeout = 1000
            idn = instrument.query("*IDN?").strip()
            instrument.close()
        except Exception as e:
            logger.warning(f"Failed to identify VISA resource {resource}: {e}")

        detail = self._infer_visa_resource(resource, idn)
        if idn:
            self._visa_details_cache[resource] = detail
        return detail

    def get_current_reading(self) -> dict:
        """
        Get the most recent DMM reading.

        Returns:
            Dictionary with current reading
        """
        if self.is_measuring:
            return self.latest_reading.copy()

        return {
            "time": None,
            "dmm1_voltage": None,
            "dmm2_voltage": None,
            "sample_index": None,
            "read_duration_ms": None,
            "loop_duration_ms": None,
            "late_by_ms": None,
            "overrun": False,
        }

    def get_current_session_data(self, limit: int = 6000) -> list[dict]:
        """Return recent data from the active/most recent in-memory session."""
        return self.data_logger.get_recent_data(limit)
