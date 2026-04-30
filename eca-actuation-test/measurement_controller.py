"""Main measurement controller coordinating all instruments."""

import asyncio
import logging
import time
from typing import Optional
import pyvisa

from instruments import KeithleyDMM, IT6412PowerSupply, USB_RLY08C
from instruments.mock import MockKeithleyDMM, MockIT6412PowerSupply, MockUSB_RLY08C
from camera_controller import CameraController
from data_logger import DataLogger
from api_models import MeasurementConfig, VoltageStage, RelayStage

logger = logging.getLogger(__name__)


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
                use_mock = len(available_devices) == 0
                if use_mock:
                    logger.warning("No VISA devices detected - using MOCK instruments")
                else:
                    logger.info(f"Found {len(available_devices)} VISA devices")
            except Exception as e:
                logger.warning(f"Error detecting VISA devices: {e} - using MOCK instruments")
                use_mock = True
        
        # Initialize instruments based on mock mode
        if use_mock:
            logger.info("Initializing MOCK instruments for testing")
            self.dmm1 = MockKeithleyDMM(name="DMM1")
            self.dmm2 = MockKeithleyDMM(name="DMM2")
            self.power_supply = MockIT6412PowerSupply()
            self.relay_board = MockUSB_RLY08C()
            self.use_mock = True
        else:
            logger.info("Initializing REAL instruments")
            self.dmm1 = KeithleyDMM()
            self.dmm2 = KeithleyDMM()
            self.power_supply = IT6412PowerSupply()
            self.relay_board = USB_RLY08C()
            self.use_mock = False
        
        self.camera = CameraController()
        self.data_logger = DataLogger()

        self.is_measuring = False
        self.current_session_id: Optional[str] = None
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
            "sample_count": 0,
            "overrun_count": 0,
            "achieved_rate_hz": None,
            "last_read_duration_ms": None,
            "last_loop_duration_ms": None,
            "last_late_by_ms": None,
        }
        
        self._measurement_task: Optional[asyncio.Task] = None
        self._voltage_stage_task: Optional[asyncio.Task] = None
        self._camera_start_task: Optional[asyncio.Task] = None
        self._camera_start_log_task: Optional[asyncio.Task] = None
        self._record_camera_for_session = False
        self._relay_stage_tasks: list[asyncio.Task] = []
        self._relay_lock = asyncio.Lock()

    async def start_measurement(self, config: MeasurementConfig) -> str:
        """
        Start a measurement with the given configuration.

        Args:
            config: Measurement configuration

        Returns:
            Session ID

        Raises:
            RuntimeError: If measurement is already running or instruments fail to connect
        """
        if self.is_measuring:
            raise RuntimeError("Measurement already in progress")

        logger.info("Starting measurement...")
        self._validate_config(config)

        # Create new session
        self.current_session_id = self.data_logger.create_session(config.test_name)
        
        # Save configuration
        self.data_logger.save_config(config.model_dump())
        self.data_logger.append_log("Session created; preparing hardware")

        try:
            await self._connect_instruments(config)

            self._record_camera_for_session = False
            if config.record_camera:
                await self._prepare_camera_for_sync(config.camera_ready_delay_seconds)
            else:
                self.data_logger.append_log("Camera recording disabled")
        except Exception as e:
            logger.error(f"Failed to start measurement: {e}")
            self.data_logger.append_log(f"ERROR: Failed to start measurement: {e}")
            if self.camera.is_recording:
                await self.camera.stop_recording()
            self._disconnect_instruments()
            self.current_session_id = None
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
            "sample_count": 0,
            "overrun_count": 0,
            "achieved_rate_hz": None,
            "last_read_duration_ms": None,
            "last_loop_duration_ms": None,
            "last_late_by_ms": None,
        }

        self._camera_start_task = None
        self._camera_start_log_task = None
        self.data_logger.append_log("Measurement t0")

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

        # Start DMM acquisition task as close as possible to the camera command.
        self._measurement_task = asyncio.create_task(
            self._dmm_acquisition_loop(config.sampling_rate_hz)
        )

        # Start voltage stage task
        if config.voltage_stages:
            self._voltage_stage_task = asyncio.create_task(
                self._execute_voltage_stages(config.voltage_stages)
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

        logger.info(f"Measurement started: {self.current_session_id}")
        return self.current_session_id

    async def _prepare_camera_for_sync(self, ready_delay_seconds: float):
        """Prepare the camera before t0 without starting recording."""
        camera_prepared = await self.camera.prepare()
        if not camera_prepared:
            self.data_logger.append_log("ERROR: Camera requested but not prepared")
            raise RuntimeError(
                "Camera recording was requested, but the camera could not be prepared. "
                "Power-cycle or replug the camera, then verify `camera/CameraControl detect`."
            )

        self.data_logger.append_log("Camera prepared")
        self._record_camera_for_session = True

        if ready_delay_seconds > 0:
            self.data_logger.append_log(f"Camera ready delay {ready_delay_seconds:.3f} s")
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
            self.data_logger.append_log(
                "ERROR: Camera did not start; command requested "
                f"{camera_start_request_offset_ms:.3f} ms after measurement t0; "
                f"failed/acknowledged {camera_start_ack_ms:.3f} ms after measurement t0"
            )
            return

        command_detail = (
            f"; camera command {camera_command_ms:.3f} ms"
            if camera_command_ms is not None
            else ""
        )
        self.data_logger.append_log(
            "Camera start command requested "
            f"{camera_start_request_offset_ms:.3f} ms after measurement t0; "
            f"acknowledged {camera_start_ack_ms:.3f} ms after measurement t0"
            f"{command_detail}"
        )

    async def stop_measurement(self) -> dict:
        """
        Stop the current measurement.

        Returns:
            Dictionary with session information and file paths
        """
        if not self.is_measuring:
            raise RuntimeError("No measurement in progress")

        logger.info("Stopping measurement...")
        self.is_measuring = False

        # Cancel all tasks
        if self._measurement_task:
            self._measurement_task.cancel()
            try:
                await self._measurement_task
            except asyncio.CancelledError:
                pass

        if self._voltage_stage_task:
            self._voltage_stage_task.cancel()
            try:
                await self._voltage_stage_task
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

        # Stop camera
        if self._record_camera_for_session and self.camera.is_recording:
            await self.camera.stop_recording()
            self.data_logger.append_log("Camera recording stopped")
        elif self._record_camera_for_session:
            self.data_logger.append_log("Camera was requested but was not recording at stop")

        # Stop data logging
        self.data_logger.stop_logging()

        # Disconnect instruments
        self._disconnect_instruments()

        # Prepare response
        session_dir = self.data_logger.current_session_dir
        response = {
            "session_id": self.current_session_id,
            "csv_path": str(self.data_logger.csv_file),
            "config_path": str(self.data_logger.config_file),
            "log_path": str(self.data_logger.log_file)
        }

        self.data_logger.append_log("Measurement stopped")
        logger.info(f"Measurement stopped: {self.current_session_id}")

        self.current_session_id = None
        self.start_time = None
        self._start_monotonic = None

        return response

    def _validate_config(self, config: MeasurementConfig):
        """Fail early when the UI submitted an incomplete hardware schedule."""
        if config.voltage_stages and not config.power_supply_visa_id:
            raise RuntimeError("Power supply stages require a selected IT6412 VISA ID")

        if (config.relay_ch1_stages or config.relay_ch2_stages) and not config.relay_port:
            raise RuntimeError("Relay stages require a selected relay board serial port")

        if config.dmm1_visa_id and config.dmm2_visa_id and config.dmm1_visa_id == config.dmm2_visa_id:
            raise RuntimeError("DMM1 and DMM2 must use different VISA IDs")

        for index, stage in enumerate(config.voltage_stages, start=1):
            if stage.end_time <= stage.start_time:
                raise RuntimeError(f"Power stage {index} end time must be after start time")

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
            # Auto-connect mock instruments
            self.dmm1.connect(config.dmm1_visa_id or "MOCK::DMM::DMM1::INSTR")
            logger.info(f"Mock DMM1 connected")
            
            self.dmm2.connect(config.dmm2_visa_id or "MOCK::DMM::DMM2::INSTR")
            logger.info(f"Mock DMM2 connected")
            
            self.power_supply.connect(config.power_supply_visa_id or "MOCK::POWER::IT6412::INSTR")
            logger.info(f"Mock Power Supply connected")
            
            self.relay_board.connect(config.relay_port or "MOCK_COM3")
            logger.info(f"Mock Relay Board connected")
        else:
            # Connect real instruments only if VISA ID provided
            if config.dmm1_visa_id:
                success = self.dmm1.connect(config.dmm1_visa_id)
                if not success:
                    raise RuntimeError(f"Failed to connect DMM1: {config.dmm1_visa_id}")
                self.dmm1.configure_fast_dc_voltage()
                logger.info(f"DMM1 connected: {config.dmm1_visa_id}")

            if config.dmm2_visa_id:
                success = self.dmm2.connect(config.dmm2_visa_id)
                if not success:
                    raise RuntimeError(f"Failed to connect DMM2: {config.dmm2_visa_id}")
                self.dmm2.configure_fast_dc_voltage()
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

            await self._prime_dmm_reads()

    async def _prime_dmm_reads(self):
        """Perform one unlogged read so DMM setup latency does not hit sample 0."""
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
        self.power_supply.disconnect()
        self.relay_board.disconnect()
        logger.info("All instruments disconnected")

    async def _dmm_acquisition_loop(self, sampling_rate_hz: float):
        """
        Continuous DMM data acquisition loop.

        Args:
            sampling_rate_hz: Sampling rate in Hz
        """
        interval = 1.0 / sampling_rate_hz
        next_sample_at = time.perf_counter()
        sample_index = 0
        logger.info(f"DMM acquisition started at {sampling_rate_hz} Hz")

        try:
            while self.is_measuring:
                now = time.perf_counter()
                if now < next_sample_at:
                    await asyncio.sleep(next_sample_at - now)

                loop_start = time.perf_counter()
                late_by_ms = max(0.0, (loop_start - next_sample_at) * 1000)

                # Get current elapsed time
                elapsed = loop_start - self._start_monotonic

                read_start = time.perf_counter()
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
                        "DMM acquisition overrun: requested %.3f ms, last loop %.3f ms",
                        interval * 1000,
                        loop_duration_ms,
                    )

                # Sleep for the remainder of the interval
                sample_index += 1
                next_sample_at += interval
                if next_sample_at < time.perf_counter():
                    next_sample_at = time.perf_counter()

        except asyncio.CancelledError:
            logger.info("DMM acquisition stopped")
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
                self.data_logger.append_log(f"Voltage set to {stage.voltage}V")

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
                self.data_logger.append_log(f"Relay CH{channel} set to {stage.state}")

                # Wait until stage end time
                while True:
                    elapsed = time.perf_counter() - self._start_monotonic
                    if elapsed >= stage.end_time:
                        break
                    await asyncio.sleep(0.01)

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
        if self.is_measuring and self.start_time:
            elapsed = round(time.time() - self.start_time, 1)

        return {
            "is_measuring": self.is_measuring,
            "camera_recording": self.camera.is_recording,
            "camera_available": self.camera.is_available,
            "session_id": self.current_session_id,
            "elapsed_time": elapsed,
            "mock_mode": self.use_mock,
            "acquisition": self.acquisition_stats,
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
            visa_details = [
                {
                    "resource": resource,
                    "idn": resource,
                    "kind": "power_supply" if "POWER" in resource else "dmm",
                    "label": resource,
                }
                for resource in visa_resources
            ]
        else:
            visa_resources = self.dmm1.list_available_devices()
            serial_ports = USB_RLY08C.list_available_ports()
            visa_details = [self._identify_visa_resource(resource) for resource in visa_resources]

        dmm_resources = [item["resource"] for item in visa_details if item["kind"] == "dmm"]
        power_supply_resources = [
            item["resource"] for item in visa_details if item["kind"] == "power_supply"
        ]

        return {
            "visa_resources": visa_resources,
            "dmm_resources": dmm_resources or visa_resources,
            "power_supply_resources": power_supply_resources or visa_resources,
            "visa_details": visa_details,
            "serial_ports": serial_ports
        }

    def _identify_visa_resource(self, resource: str) -> dict:
        """Query *IDN? and classify VISA resources for safer UI selection."""
        idn = None
        kind = "unknown"

        try:
            rm = pyvisa.ResourceManager()
            instrument = rm.open_resource(resource)
            instrument.timeout = 1000
            idn = instrument.query("*IDN?").strip()
            instrument.close()
        except Exception as e:
            logger.warning(f"Failed to identify VISA resource {resource}: {e}")

        upper_idn = (idn or "").upper()
        upper_resource = resource.upper()

        if "KEITHLEY" in upper_idn or "2110" in upper_idn:
            kind = "dmm"
        elif "IT6412" in upper_idn or "ITECH" in upper_idn or "IT-M" in upper_idn:
            kind = "power_supply"
        elif "11975::25618" in upper_resource:
            kind = "power_supply"
        elif "1510::8464" in upper_resource:
            kind = "dmm"

        label_prefix = {
            "dmm": "DMM",
            "power_supply": "Power",
        }.get(kind, "VISA")
        label = f"{label_prefix}: {idn or resource}"

        return {
            "resource": resource,
            "idn": idn,
            "kind": kind,
            "label": label,
        }

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
