"""Pydantic models for API requests and responses."""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class VoltageStage(BaseModel):
    """Voltage stage configuration."""
    start_time: float = Field(..., description="Start time in seconds", ge=0)
    end_time: float = Field(..., description="End time in seconds", ge=0)
    voltage: float = Field(..., description="Voltage to apply in volts")


class RelayStage(BaseModel):
    """Relay stage configuration."""
    start_time: float = Field(..., description="Start time in seconds", ge=0)
    end_time: float = Field(..., description="End time in seconds", ge=0)
    state: Literal["open", "closed"] = Field(..., description="Relay state")


class MeasurementConfig(BaseModel):
    """Configuration for a measurement session."""
    test_name: str = Field(default="test", description="Name for this test")
    dmm1_visa_id: Optional[str] = Field(None, description="VISA ID for DMM1")
    dmm2_visa_id: Optional[str] = Field(None, description="VISA ID for DMM2")
    power_supply_visa_id: Optional[str] = Field(None, description="VISA ID for power supply")
    relay_port: Optional[str] = Field(None, description="Serial port for relay board")
    voltage_stages: list[VoltageStage] = Field(default_factory=list, max_length=10)
    relay_ch1_stages: list[RelayStage] = Field(default_factory=list, max_length=10)
    relay_ch2_stages: list[RelayStage] = Field(default_factory=list, max_length=10)
    sampling_rate_hz: float = Field(default=10.0, description="DMM sampling rate in Hz", gt=0)


class StartMeasurementRequest(BaseModel):
    """Request to start a measurement."""
    config: MeasurementConfig


class StopMeasurementResponse(BaseModel):
    """Response after stopping a measurement."""
    session_id: str
    csv_path: str
    config_path: str
    log_path: str


class InstrumentStatus(BaseModel):
    """Status of a single instrument."""
    name: str
    connected: bool
    address: Optional[str] = None


class SystemStatus(BaseModel):
    """Overall system status."""
    is_measuring: bool
    camera_recording: bool
    camera_available: bool
    session_id: Optional[str] = None
    instruments: list[InstrumentStatus]
    elapsed_time: Optional[float] = None


class InstrumentListResponse(BaseModel):
    """List of available instruments."""
    visa_resources: list[str]
    serial_ports: list[str]


class DMMReading(BaseModel):
    """Real-time DMM reading."""
    time: float
    dmm1_voltage: Optional[float]
    dmm2_voltage: Optional[float]


class SessionInfo(BaseModel):
    """Information about a measurement session."""
    session_id: str
    path: str
    files: list[dict]
    config: Optional[dict] = None

