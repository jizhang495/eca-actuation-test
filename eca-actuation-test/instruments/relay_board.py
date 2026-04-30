"""Devantech USB-RLY08C Relay Board driver."""

import logging
import time
from typing import Optional
import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)


class USB_RLY08C:
    """Driver for Devantech USB-RLY08C 8-channel relay board."""

    # Command bytes
    CMD_GET_SERIAL = 0x38
    CMD_SET_RELAY = 0x5A
    CMD_GET_RELAY_STATE = 0x5B
    CMD_SET_PORT_DIR = 0x5C

    def __init__(self, port: Optional[str] = None, baudrate: int = 9600):
        """
        Initialize relay board connection.

        Args:
            port: Serial port (e.g., 'COM3' on Windows, '/dev/ttyUSB0' on Linux)
            baudrate: Baud rate (default: 9600)
        """
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
        self._is_connected = False
        self._relay_states = 0x00  # All relays off initially

    @staticmethod
    def list_available_ports() -> list[str]:
        """List all available serial ports."""
        try:
            ports = serial.tools.list_ports.comports()
            usb_ports = [
                port.device
                for port in ports
                if port.vid is not None or port.device.startswith(("/dev/ttyUSB", "/dev/ttyACM"))
            ]
            return usb_ports or [port.device for port in ports]
        except Exception as e:
            logger.error(f"Failed to list serial ports: {e}")
            return []

    def connect(self, port: Optional[str] = None) -> bool:
        """
        Connect to the relay board.

        Args:
            port: Serial port. If None, uses stored port.

        Returns:
            True if connection successful, False otherwise.
        """
        if port:
            self.port = port

        if not self.port:
            logger.error("No serial port specified")
            return False

        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1.0,
                write_timeout=1.0
            )
            time.sleep(0.1)  # Allow time for connection to stabilize
            self._is_connected = True
            
            # Test connection by getting relay state
            state = self.get_relay_states()
            if state is not None:
                logger.info(f"Connected to relay board on {self.port}")
                self._relay_states = state
                return True
            else:
                logger.error("Failed to communicate with relay board")
                self.disconnect()
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to relay board on {self.port}: {e}")
            self._is_connected = False
            return False

    def disconnect(self):
        """Disconnect from the relay board."""
        try:
            if self.serial and self.serial.is_open:
                # Turn all relays off before disconnecting
                self.set_all_relays_off()
                self.serial.close()
                logger.info("Relay board disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting relay board: {e}")
        finally:
            self._is_connected = False
            self.serial = None

    def set_relay(self, channel: int, state: bool) -> bool:
        """
        Set a single relay on or off.

        Args:
            channel: Relay channel (1-8)
            state: True for ON, False for OFF

        Returns:
            True if successful, False otherwise
        """
        if not self._is_connected or not self.serial:
            logger.warning("Relay board not connected")
            return False

        if not 1 <= channel <= 8:
            logger.error(f"Invalid channel: {channel}. Must be 1-8.")
            return False

        try:
            # Update relay state byte
            bit_pos = channel - 1
            if state:
                self._relay_states |= (1 << bit_pos)  # Set bit
            else:
                self._relay_states &= ~(1 << bit_pos)  # Clear bit

            # Send command
            self.serial.write(bytes([self.CMD_SET_RELAY, self._relay_states]))
            time.sleep(0.01)  # Small delay for relay to switch
            
            logger.debug(f"Relay {channel} set to {'ON' if state else 'OFF'}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set relay {channel}: {e}")
            return False

    def set_relay_on(self, channel: int) -> bool:
        """Turn a relay on."""
        return self.set_relay(channel, True)

    def set_relay_off(self, channel: int) -> bool:
        """Turn a relay off."""
        return self.set_relay(channel, False)

    def set_all_relays_off(self) -> bool:
        """Turn all relays off."""
        if not self._is_connected or not self.serial:
            logger.warning("Relay board not connected")
            return False

        try:
            self._relay_states = 0x00
            self.serial.write(bytes([self.CMD_SET_RELAY, self._relay_states]))
            logger.info("All relays turned OFF")
            return True
        except Exception as e:
            logger.error(f"Failed to turn all relays off: {e}")
            return False

    def get_relay_states(self) -> Optional[int]:
        """
        Get current state of all relays.

        Returns:
            Byte representing relay states (bit 0 = relay 1, etc.), or None if error
        """
        if not self._is_connected or not self.serial:
            logger.warning("Relay board not connected")
            return None

        try:
            self.serial.write(bytes([self.CMD_GET_RELAY_STATE]))
            time.sleep(0.05)  # Wait for response
            
            if self.serial.in_waiting > 0:
                response = self.serial.read(1)
                if response:
                    self._relay_states = response[0]
                    return self._relay_states
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get relay states: {e}")
            return None

    def get_relay_state(self, channel: int) -> Optional[bool]:
        """
        Get state of a single relay.

        Args:
            channel: Relay channel (1-8)

        Returns:
            True if ON, False if OFF, None if error
        """
        if not 1 <= channel <= 8:
            logger.error(f"Invalid channel: {channel}. Must be 1-8.")
            return None

        states = self.get_relay_states()
        if states is None:
            return None

        bit_pos = channel - 1
        return bool(states & (1 << bit_pos))

    @property
    def is_connected(self) -> bool:
        """Check if relay board is connected."""
        return self._is_connected and self.serial is not None and self.serial.is_open

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
