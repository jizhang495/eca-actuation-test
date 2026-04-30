"""Keithley 2110 Digital Multimeter driver."""

import logging
from typing import Optional
import pyvisa

logger = logging.getLogger(__name__)


class KeithleyDMM:
    """Driver for Keithley 2110 Digital Multimeter."""

    def __init__(self, visa_address: Optional[str] = None):
        """
        Initialize DMM connection.

        Args:
            visa_address: VISA resource string (e.g., 'USB0::0x05E6::0x2110::1234567::INSTR')
        """
        self.visa_address = visa_address
        self.instrument: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        self._is_connected = False

    def list_available_devices(self) -> list[str]:
        """List all available VISA devices."""
        try:
            resources = self.rm.list_resources()
            return [str(res) for res in resources if str(res).upper().startswith("USB")]
        except Exception as e:
            logger.error(f"Failed to list VISA resources: {e}")
            return []

    def connect(self, visa_address: Optional[str] = None) -> bool:
        """
        Connect to the DMM.

        Args:
            visa_address: VISA resource string. If None, uses stored address.

        Returns:
            True if connection successful, False otherwise.
        """
        if visa_address:
            self.visa_address = visa_address

        if not self.visa_address:
            logger.error("No VISA address specified")
            return False

        try:
            self.instrument = self.rm.open_resource(self.visa_address)
            self.instrument.timeout = 5000  # 5 second timeout
            
            # Test connection
            idn = self.instrument.query("*IDN?")
            logger.info(f"Connected to DMM: {idn.strip()}")
            
            # Configure for DC voltage measurement
            self.instrument.write("CONF:VOLT:DC")
            self._is_connected = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to DMM at {self.visa_address}: {e}")
            self._is_connected = False
            return False

    def disconnect(self):
        """Disconnect from the DMM."""
        try:
            if self.instrument:
                self.instrument.close()
                logger.info("DMM disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting DMM: {e}")
        finally:
            self._is_connected = False
            self.instrument = None

    def read_voltage(self) -> Optional[float]:
        """
        Read voltage from DMM.

        Returns:
            Voltage reading in volts, or None if error.
        """
        if not self._is_connected or not self.instrument:
            logger.warning("DMM not connected")
            return None

        try:
            # Read voltage
            voltage_str = self.instrument.query("READ?")
            voltage = float(voltage_str.strip())
            return voltage
        except Exception as e:
            logger.error(f"Failed to read voltage: {e}")
            return None

    def configure_dc_voltage(self, range_val: float = 10.0):
        """
        Configure DMM for DC voltage measurement.

        Args:
            range_val: Voltage range in volts (e.g., 1, 10, 100)
        """
        if not self._is_connected or not self.instrument:
            logger.warning("DMM not connected")
            return

        try:
            self.instrument.write(f"CONF:VOLT:DC {range_val}")
            logger.info(f"DMM configured for DC voltage, range: {range_val}V")
        except Exception as e:
            logger.error(f"Failed to configure DMM: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if DMM is connected."""
        return self._is_connected

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
