"""IT6412 Bipolar DC Power Supply driver."""

import logging
from typing import Optional
import pyvisa

logger = logging.getLogger(__name__)


class IT6412PowerSupply:
    """Driver for IT6412 Bipolar DC Power Supply."""

    def __init__(self, visa_address: Optional[str] = None):
        """
        Initialize power supply connection.

        Args:
            visa_address: VISA resource string
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
        Connect to the power supply.

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
            logger.info(f"Connected to Power Supply: {idn.strip()}")
            self._is_connected = True
            
            # Initialize to safe state
            self.set_voltage(0.0)
            self.set_output_off()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to power supply at {self.visa_address}: {e}")
            self._is_connected = False
            return False

    def disconnect(self):
        """Disconnect from the power supply."""
        try:
            if self.instrument:
                # Set to safe state before disconnecting
                self.set_voltage(0.0)
                self.set_output_off()
                self.instrument.close()
                logger.info("Power supply disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting power supply: {e}")
        finally:
            self._is_connected = False
            self.instrument = None

    def set_voltage(self, voltage: float) -> bool:
        """
        Set output voltage.

        Args:
            voltage: Voltage to set in volts

        Returns:
            True if successful, False otherwise
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return False

        try:
            self.instrument.write(f"VOLT {voltage}")
            logger.debug(f"Set voltage to {voltage}V")
            return True
        except Exception as e:
            logger.error(f"Failed to set voltage: {e}")
            return False

    def set_current_limit(self, current: float) -> bool:
        """
        Set current limit.

        Args:
            current: Current limit in amps

        Returns:
            True if successful, False otherwise
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return False

        try:
            self.instrument.write(f"CURR {current}")
            logger.debug(f"Set current limit to {current}A")
            return True
        except Exception as e:
            logger.error(f"Failed to set current limit: {e}")
            return False

    def set_output_on(self) -> bool:
        """
        Turn output on.

        Returns:
            True if successful, False otherwise
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return False

        try:
            self.instrument.write("OUTP ON")
            logger.info("Output turned ON")
            return True
        except Exception as e:
            logger.error(f"Failed to turn output on: {e}")
            return False

    def set_output_off(self) -> bool:
        """
        Turn output off.

        Returns:
            True if successful, False otherwise
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return False

        try:
            self.instrument.write("OUTP OFF")
            logger.info("Output turned OFF")
            return True
        except Exception as e:
            logger.error(f"Failed to turn output off: {e}")
            return False

    def measure_voltage(self) -> Optional[float]:
        """
        Measure actual output voltage.

        Returns:
            Voltage in volts, or None if error
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return None

        try:
            voltage_str = self.instrument.query("MEAS:VOLT?")
            return float(voltage_str.strip())
        except Exception as e:
            logger.error(f"Failed to measure voltage: {e}")
            return None

    def measure_current(self) -> Optional[float]:
        """
        Measure actual output current.

        Returns:
            Current in amps, or None if error
        """
        if not self._is_connected or not self.instrument:
            logger.warning("Power supply not connected")
            return None

        try:
            current_str = self.instrument.query("MEAS:CURR?")
            return float(current_str.strip())
        except Exception as e:
            logger.error(f"Failed to measure current: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        """Check if power supply is connected."""
        return self._is_connected

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
