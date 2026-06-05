"""Mock instrument drivers for testing without hardware."""

import logging
import time
import random
from typing import Optional

logger = logging.getLogger(__name__)


class MockKeithleyDMM:
    """Mock Keithley 2110 Digital Multimeter."""

    def __init__(self, visa_address: Optional[str] = None, name: str = "MockDMM"):
        """Initialize mock DMM."""
        self.visa_address = visa_address or f"MOCK::DMM::{name}::INSTR"
        self._is_connected = False
        self._base_voltage = 0.0
        self._noise_level = 0.001  # 1mV noise
        self.name = name

    def list_available_devices(self) -> list[str]:
        """List mock VISA devices."""
        return [
            "MOCK::DMM::DMM1::INSTR",
            "MOCK::DMM::DMM2::INSTR",
            "MOCK::SCOPE::OSC1::INSTR",
            "MOCK::POWER::IT6412::INSTR",
        ]

    def connect(self, visa_address: Optional[str] = None) -> bool:
        """Simulate connection."""
        if visa_address:
            self.visa_address = visa_address
        
        logger.info(f"Mock DMM ({self.name}) connected: {self.visa_address}")
        self._is_connected = True
        return True

    def disconnect(self):
        """Simulate disconnection."""
        logger.info(f"Mock DMM ({self.name}) disconnected")
        self._is_connected = False

    def read_voltage(self) -> Optional[float]:
        """
        Simulate voltage reading with realistic noise.
        
        Returns:
            Simulated voltage reading in volts
        """
        if not self._is_connected:
            return None

        # Add some realistic noise and drift
        noise = random.gauss(0, self._noise_level)
        drift = 0.0001 * random.uniform(-1, 1)
        
        # Simulate realistic voltage measurement
        voltage = self._base_voltage + noise + drift
        
        return voltage

    def configure_dc_voltage(self, range_val: float = 10.0):
        """Simulate configuration."""
        if self._is_connected:
            logger.debug(f"Mock DMM ({self.name}) configured for DC voltage, range: {range_val}V")

    def configure_acquisition_mode(self, mode: str = "fast", range_val: float = 10.0):
        """Simulate acquisition mode configuration."""
        if self._is_connected:
            logger.debug(
                "Mock DMM (%s) configured for mode %s, range: %sV",
                self.name,
                mode,
                range_val,
            )

    def configure_fast_dc_voltage(self, range_val: float = 10.0, nplc: float = 0.02):
        """Simulate fast configuration."""
        self.configure_acquisition_mode("fast", range_val)

    def set_base_voltage(self, voltage: float):
        """Set the base voltage for simulation (for testing)."""
        self._base_voltage = voltage

    @property
    def is_connected(self) -> bool:
        """Check if mock DMM is connected."""
        return self._is_connected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


class MockIT6412PowerSupply:
    """Mock IT6412 Bipolar DC Power Supply."""

    def __init__(self, visa_address: Optional[str] = None):
        """Initialize mock power supply."""
        self.visa_address = visa_address or "MOCK::POWER::IT6412::INSTR"
        self._is_connected = False
        self._voltage = 0.0
        self._current_limit = 1.0
        self._output_on = False

    def list_available_devices(self) -> list[str]:
        """List mock VISA devices."""
        return [
            "MOCK::DMM::DMM1::INSTR",
            "MOCK::DMM::DMM2::INSTR",
            "MOCK::SCOPE::OSC1::INSTR",
            "MOCK::POWER::IT6412::INSTR",
        ]

    def connect(self, visa_address: Optional[str] = None) -> bool:
        """Simulate connection."""
        if visa_address:
            self.visa_address = visa_address
        
        logger.info(f"Mock Power Supply connected: {self.visa_address}")
        self._is_connected = True
        self._voltage = 0.0
        self._output_on = False
        return True

    def disconnect(self):
        """Simulate disconnection."""
        self._voltage = 0.0
        self._output_on = False
        logger.info("Mock Power Supply disconnected")
        self._is_connected = False

    def set_voltage(self, voltage: float) -> bool:
        """Simulate setting voltage."""
        if not self._is_connected:
            return False
        
        self._voltage = voltage
        logger.debug(f"Mock Power Supply voltage set to {voltage}V")
        return True

    def set_current_limit(self, current: float) -> bool:
        """Simulate setting current limit."""
        if not self._is_connected:
            return False
        
        self._current_limit = current
        logger.debug(f"Mock Power Supply current limit set to {current}A")
        return True

    def set_output_on(self) -> bool:
        """Simulate turning output on."""
        if not self._is_connected:
            return False
        
        self._output_on = True
        logger.info("Mock Power Supply output turned ON")
        return True

    def set_output_off(self) -> bool:
        """Simulate turning output off."""
        if not self._is_connected:
            return False
        
        self._output_on = False
        logger.info("Mock Power Supply output turned OFF")
        return True

    def measure_voltage(self) -> Optional[float]:
        """Simulate voltage measurement."""
        if not self._is_connected:
            return None
        
        # Add small noise to simulate real measurement
        noise = random.gauss(0, 0.001)
        return self._voltage + noise if self._output_on else 0.0

    def measure_current(self) -> Optional[float]:
        """Simulate current measurement."""
        if not self._is_connected:
            return None
        
        # Simulate some load current
        if self._output_on and self._voltage > 0:
            current = min(self._voltage * 0.1, self._current_limit)  # Simple Ohm's law simulation
            noise = random.gauss(0, 0.0001)
            return current + noise
        return 0.0

    @property
    def is_connected(self) -> bool:
        """Check if mock power supply is connected."""
        return self._is_connected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


class MockOscilloscope:
    """Mock two-channel oscilloscope."""

    def __init__(self, visa_address: Optional[str] = None):
        self.visa_address = visa_address or "MOCK::SCOPE::OSC1::INSTR"
        self._is_connected = False
        self._started_at = time.monotonic()
        self._noise_level = 0.002

    def list_available_devices(self) -> list[str]:
        """List mock VISA devices."""
        return [
            "MOCK::DMM::DMM1::INSTR",
            "MOCK::DMM::DMM2::INSTR",
            "MOCK::SCOPE::OSC1::INSTR",
            "MOCK::POWER::IT6412::INSTR",
        ]

    def connect(self, visa_address: Optional[str] = None) -> bool:
        if visa_address:
            self.visa_address = visa_address

        logger.info(f"Mock Oscilloscope connected: {self.visa_address}")
        self._started_at = time.monotonic()
        self._is_connected = True
        return True

    def disconnect(self):
        logger.info("Mock Oscilloscope disconnected")
        self._is_connected = False

    def configure_voltage_channels(self):
        if self._is_connected:
            logger.debug("Mock oscilloscope configured for CH1/CH2 voltage measurements")

    def read_voltages(self) -> tuple[Optional[float], Optional[float]]:
        return (self.read_channel_voltage(1), self.read_channel_voltage(2))

    def read_channel_voltage(self, channel: int) -> Optional[float]:
        if not self._is_connected:
            return None

        elapsed = time.monotonic() - self._started_at
        noise = random.gauss(0, self._noise_level)
        if channel == 1:
            return 0.25 * random.uniform(-1, 1) + noise
        if channel == 2:
            return 0.5 + 0.05 * (elapsed % 10) + noise

        raise ValueError("Only oscilloscope channels 1 and 2 are supported")

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


class MockMokuProDatalogger:
    """Mock Moku:Pro Data Logger."""

    def __init__(self, moku_address: Optional[str] = None):
        self.moku_address = moku_address or "MOKU::MOCK::PRO"
        self._is_connected = False
        self._logging_started_at: Optional[float] = None
        self._sample_rate_hz = 1000.0
        self._duration_seconds = 0.0
        self._waveform_generator = {
            "channel": 1,
            "waveform": "Sine",
            "vpp": 0.0,
            "frequency_hz": 1.0,
        }

    @staticmethod
    def list_available_devices() -> list[str]:
        return ["MOKU::MOCK::PRO"]

    @staticmethod
    def discover_devices() -> list[dict]:
        return [
            {
                "resource": "MOKU::MOCK::PRO",
                "idn": "MokuPro-MOCK, serial MOCK, Moku:Pro",
                "kind": "moku",
                "label": "Moku:Pro MOCK",
            }
        ]

    def connect(self, moku_address: Optional[str] = None) -> bool:
        if moku_address:
            self.moku_address = moku_address
        logger.info("Mock Moku:Pro connected: %s", self.moku_address)
        self._is_connected = True
        return True

    def configure_voltage_channels(self, sample_rate_hz: float):
        self._sample_rate_hz = sample_rate_hz

    def start_logging(
        self,
        duration_seconds: float,
        sample_rate_hz: float,
        file_name_prefix: str,
        comments: str = "",
    ) -> dict:
        self._logging_started_at = time.monotonic()
        self._sample_rate_hz = sample_rate_hz
        self._duration_seconds = duration_seconds
        return {
            "file_name": f"{file_name_prefix}_mock.li",
            "duration": duration_seconds,
            "rate": sample_rate_hz,
            "comments": comments,
        }

    def stop_logging(self) -> dict:
        return {"stopped": True}

    def generate_waveform(
        self,
        waveform: str,
        vpp: float,
        frequency_hz: float,
        channel: int = 1,
    ) -> dict:
        self._waveform_generator = {
            "channel": channel,
            "waveform": waveform,
            "vpp": vpp,
            "frequency_hz": frequency_hz,
        }
        return {"generated": True, **self._waveform_generator}

    def stop_waveform_generator(self, channel: int = 1) -> dict:
        return self.generate_waveform("Sine", 0.0, 1.0, channel=channel)

    def generate_signal(
        self,
        waveform: str,
        vpp: float,
        frequency_hz: float,
        channel: int = 1,
    ) -> dict:
        return self.generate_waveform(waveform, vpp, frequency_hz, channel=channel)

    def stop_signal_generator(self, channel: int = 1) -> dict:
        return self.stop_waveform_generator(channel=channel)

    def capture_waveforms(
        self,
        session_dir,
        stop_elapsed_seconds: Optional[float],
        t0_offset_seconds: Optional[float],
    ) -> dict:
        duration = stop_elapsed_seconds or min(self._duration_seconds, 10.0)
        sample_count = max(2, min(int(duration * self._sample_rate_hz), 10000))
        dt = duration / (sample_count - 1) if sample_count > 1 else 0.0
        rows = []
        for index in range(sample_count):
            t = index * dt
            rows.append(
                {
                    "time": t,
                    "scope_time": t + (t0_offset_seconds or 0.0),
                    "ch1_voltage": 0.2 + random.gauss(0, 0.002),
                    "ch2_voltage": 0.01 * random.gauss(0, 1.0),
                    "sample_index": index,
                    "ch1_sample_index": index,
                    "ch2_sample_index": index,
                }
            )
        return {
            "metadata": {
                "source": "moku",
                "instrument": "Mock Moku:Pro Data Logger",
                "probe_attenuation": {"ch1": 10.0, "ch2": 1.0},
                "frontend_ranges": {"ch1": "400mVpp", "ch2": "400mVpp"},
                "voltage_scaling": (
                    "mock rows already use normalized circuit voltage, matching "
                    "the real Moku waveform export"
                ),
                "requested_sample_rate_hz": self._sample_rate_hz,
                "stop_elapsed_seconds": stop_elapsed_seconds,
                "t0_offset_seconds": t0_offset_seconds,
            },
            "rows": rows,
        }

    def disconnect(self):
        logger.info("Mock Moku:Pro disconnected")
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected


class MockUSB_RLY08C:
    """Mock Devantech USB-RLY08C 8-channel relay board."""

    def __init__(self, port: Optional[str] = None, baudrate: int = 9600):
        """Initialize mock relay board."""
        self.port = port or "MOCK_COM3"
        self.baudrate = baudrate
        self._is_connected = False
        self._relay_states = 0x00  # All relays off

    @staticmethod
    def list_available_ports() -> list[str]:
        """List mock serial ports."""
        return ["MOCK_COM3", "MOCK_COM4", "MOCK_COM5"]

    def connect(self, port: Optional[str] = None) -> bool:
        """Simulate connection."""
        if port:
            self.port = port
        
        logger.info(f"Mock Relay Board connected on {self.port}")
        self._is_connected = True
        self._relay_states = 0x00
        return True

    def disconnect(self):
        """Simulate disconnection."""
        self._relay_states = 0x00
        logger.info("Mock Relay Board disconnected")
        self._is_connected = False

    def set_relay(self, channel: int, state: bool) -> bool:
        """Simulate setting a relay."""
        if not self._is_connected:
            return False
        
        if not 1 <= channel <= 8:
            logger.error(f"Invalid channel: {channel}")
            return False

        bit_pos = channel - 1
        if state:
            self._relay_states |= (1 << bit_pos)
        else:
            self._relay_states &= ~(1 << bit_pos)

        logger.debug(f"Mock Relay {channel} set to {'ON' if state else 'OFF'}")
        return True

    def set_relay_on(self, channel: int) -> bool:
        """Turn a relay on."""
        return self.set_relay(channel, True)

    def set_relay_off(self, channel: int) -> bool:
        """Turn a relay off."""
        return self.set_relay(channel, False)

    def set_all_relays_off(self) -> bool:
        """Turn all relays off."""
        if not self._is_connected:
            return False
        
        self._relay_states = 0x00
        logger.info("All mock relays turned OFF")
        return True

    def get_relay_states(self) -> Optional[int]:
        """Get current state of all relays."""
        if not self._is_connected:
            return None
        return self._relay_states

    def get_relay_state(self, channel: int) -> Optional[bool]:
        """Get state of a single relay."""
        if not 1 <= channel <= 8:
            return None
        
        states = self.get_relay_states()
        if states is None:
            return None
        
        bit_pos = channel - 1
        return bool(states & (1 << bit_pos))

    @property
    def is_connected(self) -> bool:
        """Check if mock relay board is connected."""
        return self._is_connected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
