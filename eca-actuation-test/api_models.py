"""Pydantic models for API requests and responses."""

from typing import Optional, Literal
from pydantic import BaseModel, Field

DMMAcquisitionMode = Literal["fast", "low_noise"]
ControlSource = Literal["ui", "api", "agent", "script"]


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
    dmm_acquisition_mode: DMMAcquisitionMode = Field(
        default="fast",
        description="DMM integration/noise-rejection mode",
    )
    record_camera: bool = Field(default=False, description="Start camera recording with measurement")
    camera_ready_delay_seconds: float = Field(
        default=1.0,
        description="Seconds to wait after camera prepare before synchronized t0",
        ge=0,
        le=30,
    )


class StartMeasurementRequest(BaseModel):
    """Request to start a measurement."""
    config: MeasurementConfig
    control_source: ControlSource = Field(
        default="api",
        description="Who initiated the run, used for runtime status and audit display",
    )


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


class AcquisitionStats(BaseModel):
    """Runtime acquisition timing statistics."""
    requested_rate_hz: Optional[float] = None
    dmm_acquisition_mode: Optional[DMMAcquisitionMode] = None
    sample_count: int = 0
    overrun_count: int = 0
    achieved_rate_hz: Optional[float] = None
    last_read_duration_ms: Optional[float] = None
    last_loop_duration_ms: Optional[float] = None
    last_late_by_ms: Optional[float] = None


class RuntimeEvent(BaseModel):
    """Operator/agent visible runtime event."""
    timestamp: str
    message: str
    kind: str = "info"
    source: Optional[ControlSource] = None
    elapsed_time: Optional[float] = None


class SystemStatus(BaseModel):
    """Overall system status."""
    is_measuring: bool
    camera_recording: bool
    camera_available: bool
    session_id: Optional[str] = None
    instruments: list[InstrumentStatus]
    elapsed_time: Optional[float] = None
    mock_mode: bool = False
    acquisition: AcquisitionStats = Field(default_factory=AcquisitionStats)
    active_config: Optional[MeasurementConfig] = None
    control_source: Optional[ControlSource] = None
    events: list[RuntimeEvent] = Field(default_factory=list)


class VisaResourceInfo(BaseModel):
    """Detected VISA resource metadata."""
    resource: str
    idn: Optional[str] = None
    kind: str = "unknown"
    label: str


class InstrumentListResponse(BaseModel):
    """List of available instruments."""
    visa_resources: list[str]
    dmm_resources: list[str] = Field(default_factory=list)
    power_supply_resources: list[str] = Field(default_factory=list)
    visa_details: list[VisaResourceInfo] = Field(default_factory=list)
    serial_ports: list[str]


class DMMReading(BaseModel):
    """Real-time DMM reading."""
    time: float
    dmm1_voltage: Optional[float]
    dmm2_voltage: Optional[float]
    sample_index: Optional[int] = None
    read_duration_ms: Optional[float] = None
    loop_duration_ms: Optional[float] = None
    late_by_ms: Optional[float] = None
    overrun: bool = False


class SessionInfo(BaseModel):
    """Information about a measurement session."""
    session_id: str
    path: str
    files: list[dict]
    config: Optional[dict] = None
