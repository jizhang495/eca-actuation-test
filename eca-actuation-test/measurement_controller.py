"""Main measurement controller coordinating all instruments."""

import asyncio
import logging
import time
from typing import Optional
from datetime import datetime

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
        
        self._measurement_task: Optional[asyncio.Task] = None
        self._voltage_stage_task: Optional[asyncio.Task] = None
        self._relay_stage_tasks: list[asyncio.Task] = []

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

        # Create new session
        self.current_session_id = self.data_logger.create_session(config.test_name)
        
        # Save configuration
        self.data_logger.save_config(config.model_dump())
        self.data_logger.append_log("Measurement started")

        # Connect instruments
        try:
            await self._connect_instruments(config)
        except Exception as e:
            logger.error(f"Failed to connect instruments: {e}")
            self.data_logger.append_log(f"ERROR: Failed to connect instruments: {e}")
            raise

        # Start camera recording
        camera_started = await self.camera.start_recording()
        if camera_started:
            self.data_logger.append_log("Camera recording started")
        else:
            self.data_logger.append_log("WARNING: Camera not available")

        # Start data logging
        self.data_logger.start_logging()

        # Start measurement tasks
        self.is_measuring = True
        self.start_time = time.time()

        # Start DMM acquisition task
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

        for task in self._relay_stage_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._relay_stage_tasks.clear()

        # Stop camera
        await self.camera.stop_recording()
        self.data_logger.append_log("Camera recording stopped")

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

        return response

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
                logger.info(f"DMM1 connected: {config.dmm1_visa_id}")

            if config.dmm2_visa_id:
                success = self.dmm2.connect(config.dmm2_visa_id)
                if not success:
                    raise RuntimeError(f"Failed to connect DMM2: {config.dmm2_visa_id}")
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
        logger.info(f"DMM acquisition started at {sampling_rate_hz} Hz")

        try:
            while self.is_measuring:
                loop_start = time.time()

                # Get current elapsed time
                elapsed = time.time() - self.start_time

                # Read from both DMMs
                dmm1_voltage = self.dmm1.read_voltage() if self.dmm1.is_connected else None
                dmm2_voltage = self.dmm2.read_voltage() if self.dmm2.is_connected else None

                # Log the reading
                self.data_logger.log_reading(elapsed, dmm1_voltage, dmm2_voltage)

                # Sleep for the remainder of the interval
                loop_duration = time.time() - loop_start
                sleep_time = max(0, interval - loop_duration)
                await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            logger.info("DMM acquisition stopped")
            raise

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
        self.power_supply.set_output_on()

        try:
            for i, stage in enumerate(stages):
                # Wait until stage start time
                while True:
                    elapsed = time.time() - self.start_time
                    if elapsed >= stage.start_time:
                        break
                    await asyncio.sleep(0.01)

                # Set voltage
                self.power_supply.set_voltage(stage.voltage)
                logger.info(f"Voltage stage {i+1}: {stage.voltage}V at {elapsed:.2f}s")
                self.data_logger.append_log(f"Voltage set to {stage.voltage}V")

                # Wait until stage end time
                while True:
                    elapsed = time.time() - self.start_time
                    if elapsed >= stage.end_time:
                        break
                    await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info("Voltage stages cancelled")
            raise
        finally:
            # Safe shutdown
            self.power_supply.set_voltage(0.0)
            self.power_supply.set_output_off()
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
                    elapsed = time.time() - self.start_time
                    if elapsed >= stage.start_time:
                        break
                    await asyncio.sleep(0.01)

                # Set relay state
                if stage.state == "closed":
                    self.relay_board.set_relay_on(channel)
                else:
                    self.relay_board.set_relay_off(channel)

                logger.info(f"Relay CH{channel} stage {i+1}: {stage.state} at {elapsed:.2f}s")
                self.data_logger.append_log(f"Relay CH{channel} set to {stage.state}")

                # Wait until stage end time
                while True:
                    elapsed = time.time() - self.start_time
                    if elapsed >= stage.end_time:
                        break
                    await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info(f"Relay CH{channel} stages cancelled")
            raise
        finally:
            # Safe shutdown - open relay
            self.relay_board.set_relay_off(channel)
            logger.info(f"Relay CH{channel} stages completed")

    def get_status(self) -> dict:
        """
        Get current system status.

        Returns:
            Dictionary with status information
        """
        elapsed = None
        if self.is_measuring and self.start_time:
            elapsed = time.time() - self.start_time

        return {
            "is_measuring": self.is_measuring,
            "camera_recording": self.camera.is_recording,
            "camera_available": self.camera.is_available,
            "session_id": self.current_session_id,
            "elapsed_time": elapsed,
            "mock_mode": self.use_mock,
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
        else:
            visa_resources = self.dmm1.list_available_devices()
            serial_ports = USB_RLY08C.list_available_ports()

        return {
            "visa_resources": visa_resources,
            "serial_ports": serial_ports
        }

    def get_current_reading(self) -> dict:
        """
        Get the most recent DMM reading.

        Returns:
            Dictionary with current reading
        """
        elapsed = None
        if self.start_time:
            elapsed = time.time() - self.start_time

        return {
            "time": elapsed,
            "dmm1_voltage": self.dmm1.read_voltage() if self.dmm1.is_connected else None,
            "dmm2_voltage": self.dmm2.read_voltage() if self.dmm2.is_connected else None
        }

