"""Generic SCPI oscilloscope driver for voltage measurements."""

import logging
import math
import re
from typing import Optional

import pyvisa

logger = logging.getLogger(__name__)


class Oscilloscope:
    """Generic SCPI oscilloscope driver.

    The driver reads channel mean/DC voltage with a small set of common SCPI
    command variants used by Tektronix, Keysight/Agilent, Rigol, and similar
    scopes. Unsupported commands are ignored and the next query variant is tried.
    """

    _FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")

    def __init__(self, visa_address: Optional[str] = None):
        self.visa_address = visa_address
        self.instrument: Optional[pyvisa.Resource] = None
        self.rm = pyvisa.ResourceManager()
        self._is_connected = False

    def list_available_devices(self) -> list[str]:
        """List all available USB VISA devices."""
        try:
            resources = self.rm.list_resources()
            return [str(res) for res in resources if str(res).upper().startswith("USB")]
        except Exception as e:
            logger.error(f"Failed to list VISA resources: {e}")
            return []

    def connect(self, visa_address: Optional[str] = None) -> bool:
        """Connect to the oscilloscope."""
        if visa_address:
            self.visa_address = visa_address

        if not self.visa_address:
            logger.error("No oscilloscope VISA address specified")
            return False

        try:
            self.instrument = self.rm.open_resource(self.visa_address)
            self.instrument.timeout = 5000
            idn = self.instrument.query("*IDN?").strip()
            logger.info(f"Connected to oscilloscope: {idn}")
            self._is_connected = True
            self.configure_voltage_channels()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to oscilloscope at {self.visa_address}: {e}")
            self._is_connected = False
            self.instrument = None
            return False

    def disconnect(self):
        """Disconnect from the oscilloscope."""
        try:
            if self.instrument:
                self.instrument.close()
                logger.info("Oscilloscope disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting oscilloscope: {e}")
        finally:
            self._is_connected = False
            self.instrument = None

    def configure_voltage_channels(self):
        """Best-effort configuration for channel 1 and 2 voltage measurements."""
        if not self._is_connected or not self.instrument:
            logger.warning("Oscilloscope not connected")
            return

        for channel in (1, 2):
            for command in (
                f":CHANnel{channel}:DISPlay ON",
                f":CHANnel{channel}:COUPling DC",
                f":CHAN{channel}:DISP ON",
                f":CHAN{channel}:COUP DC",
            ):
                try:
                    self.instrument.write(command)
                except Exception:
                    logger.debug("Oscilloscope config command not accepted: %s", command)

    def read_voltages(self) -> tuple[Optional[float], Optional[float]]:
        """Read channel 1 and channel 2 voltages."""
        return (self.read_channel_voltage(1), self.read_channel_voltage(2))

    def read_channel_voltage(self, channel: int) -> Optional[float]:
        """Read the mean/DC voltage for a single oscilloscope channel."""
        if channel not in (1, 2):
            raise ValueError("Only oscilloscope channels 1 and 2 are supported")

        if not self._is_connected or not self.instrument:
            logger.warning("Oscilloscope not connected")
            return None

        query_variants = (
            f":MEASure:VAVerage? CHANnel{channel}",
            f":MEASure:VAVerage? CHAN{channel}",
            f":MEASure:MEAN? CHANnel{channel}",
            f":MEASure:MEAN? CHAN{channel}",
            f":MEASure:VDC? CHANnel{channel}",
            f":MEASure:VDC? CHAN{channel}",
            f":MEASure:ITEM? VAVG,CHANnel{channel}",
            f":MEASure:ITEM? VAVG,CHAN{channel}",
            f"C{channel}:PAVA? MEAN",
            f"C{channel}:PAVA? VAVG",
        )

        for query in query_variants:
            value = self._query_float(query)
            if value is not None:
                return value

        tektronix_value = self._read_tektronix_immediate_measurement(channel)
        if tektronix_value is not None:
            return tektronix_value

        logger.error("Failed to read oscilloscope channel %s voltage", channel)
        return None

    def _read_tektronix_immediate_measurement(self, channel: int) -> Optional[float]:
        """Read voltage using Tektronix immediate measurement commands."""
        if not self.instrument:
            return None

        command_sets = (
            (
                f"MEASUrement:IMMed:SOUrce1 CH{channel}",
                "MEASUrement:IMMed:TYPe MEAN",
                "MEASUrement:IMMed:VALue?",
            ),
            (
                f"MEASU:IMM:SOU1 CH{channel}",
                "MEASU:IMM:TYP MEAN",
                "MEASU:IMM:VAL?",
            ),
        )

        for source_command, type_command, value_query in command_sets:
            try:
                self.instrument.write(source_command)
                self.instrument.write(type_command)
            except Exception:
                continue

            value = self._query_float(value_query)
            if value is not None:
                return value

        return None

    def _query_float(self, query: str) -> Optional[float]:
        if not self.instrument:
            return None

        try:
            response = str(self.instrument.query(query)).strip()
        except Exception:
            logger.debug("Oscilloscope query not accepted: %s", query)
            return None

        matches = self._FLOAT_RE.findall(response)
        if not matches:
            return None

        try:
            value = float(matches[-1])
        except ValueError:
            return None

        if not math.isfinite(value) or abs(value) > 1e30:
            return None

        return value

    @property
    def is_connected(self) -> bool:
        """Check whether the oscilloscope is connected."""
        return self._is_connected
