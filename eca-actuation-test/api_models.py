"""Pydantic models for API requests and responses."""

from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator

DMMAcquisitionMode = Literal["fast", "low_noise"]
MeasurementSource = Literal["dmm", "oscilloscope", "moku"]
ControlSource = Literal["ui", "api", "agent", "script"]
MokuWaveform = Literal["Sine", "Square", "Ramp", "Pulse"]


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


class MokuWaveformGeneratorStage(BaseModel):
    """Moku:Pro Waveform Generator stage configuration."""
    start_time: float = Field(..., description="Start time in seconds", ge=0)
    end_time: float = Field(..., description="End time in seconds", ge=0)
    waveform: MokuWaveform = Field(..., description="Waveform type")
    vpp: float = Field(..., description="Output amplitude in Vpp", ge=0)
    frequency_hz: float = Field(..., description="Output frequency in Hz", gt=0)


class MeasurementConfig(BaseModel):
    """Configuration for a measurement session."""
    test_name: str = Field(default="test", description="Name for this test")
    measurement_source: MeasurementSource = Field(
        default="oscilloscope",
        description="Instrument source for the two voltage traces",
    )
    dmm1_visa_id: Optional[str] = Field(None, description="VISA ID for DMM1")
    dmm2_visa_id: Optional[str] = Field(None, description="VISA ID for DMM2")
    oscilloscope_visa_id: Optional[str] = Field(None, description="VISA ID for oscilloscope")
    moku_address: Optional[str] = Field(None, description="Moku:Pro address or discovered resource")
    power_supply_visa_id: Optional[str] = Field(None, description="VISA ID for power supply")
    relay_port: Optional[str] = Field(None, description="Serial port for relay board")
    voltage_stages: list[VoltageStage] = Field(default_factory=list, max_length=10)
    relay_ch1_stages: list[RelayStage] = Field(default_factory=list, max_length=10)
    relay_ch2_stages: list[RelayStage] = Field(default_factory=list, max_length=10)
    moku_waveform_generator_stages: list[MokuWaveformGeneratorStage] = Field(
        default_factory=list,
        max_length=10,
        description="Moku:Pro Waveform Generator stages for output 1",
    )
    sampling_rate_hz: float = Field(default=10.0, description="DMM sampling rate in Hz", gt=0)
    moku_sample_rate_hz: float = Field(
        default=10000.0,
        description="Moku:Pro Data Logger sample rate in Hz",
        ge=10,
        le=1_000_000,
    )
    dmm_acquisition_mode: DMMAcquisitionMode = Field(
        default="fast",
        description="DMM integration/noise-rejection mode",
    )
    stop_after_seconds: Optional[float] = Field(
        default=None,
        description="Automatically stop the measurement at this elapsed time in seconds",
        gt=0,
    )
    record_camera: bool = Field(default=False, description="Start camera recording with measurement")
    auto_download_camera_recording: bool = Field(
        default=False,
        description="Download the camera recording and convert it after measurement",
    )
    camera_ready_delay_seconds: float = Field(
        default=1.0,
        description="Seconds to wait after hardware prepare before synchronized t0",
        ge=0,
        le=30,
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_moku_signal_generator_key(cls, data):
        """Accept the earlier draft key while saving the Moku API-style key."""
        if (
            isinstance(data, dict)
            and "moku_waveform_generator_stages" not in data
            and "moku_signal_generator_stages" in data
        ):
            data = dict(data)
            data["moku_waveform_generator_stages"] = data["moku_signal_generator_stages"]
        return data


class StartMeasurementRequest(BaseModel):
    """Request to start a measurement."""
    config: MeasurementConfig
    control_source: ControlSource = Field(
        default="api",
        description="Who initiated the run, used for runtime status and audit display",
    )


class SaveExperimentConfigRequest(BaseModel):
    """Request to save an experiment configuration preset."""
    config: MeasurementConfig
    file_name: Optional[str] = Field(
        default=None,
        description="Optional JSON file name. Defaults to the sanitized test name.",
    )


class SaveExperimentConfigResponse(BaseModel):
    """Response after saving an experiment configuration preset."""
    success: bool
    file_name: str
    path: str
    message: str


class StopMeasurementResponse(BaseModel):
    """Response after stopping a measurement."""
    session_id: str
    csv_path: str
    config_path: str
    log_path: str
    oscilloscope_csv_path: Optional[str] = None
    oscilloscope_metadata_path: Optional[str] = None
    moku_csv_path: Optional[str] = None
    moku_metadata_path: Optional[str] = None


class InstrumentStatus(BaseModel):
    """Status of a single instrument."""
    name: str
    connected: bool
    address: Optional[str] = None


class AcquisitionStats(BaseModel):
    """Runtime acquisition timing statistics."""
    requested_rate_hz: Optional[float] = None
    measurement_source: Optional[MeasurementSource] = None
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
    camera_timing: dict = Field(default_factory=dict)
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
    oscilloscope_resources: list[str] = Field(default_factory=list)
    moku_resources: list[str] = Field(default_factory=list)
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


class CameraDownloadStatus(BaseModel):
    """Status of the latest camera recording download task."""
    is_running: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    success: Optional[bool] = None
    message: str = "No camera download has been started"
    session_dir: Optional[str] = None
    camera_file: Optional[str] = None
    raw_destination: Optional[str] = None
    destination: Optional[str] = None
    raw_metadata_path: Optional[str] = None
    metadata_path: Optional[str] = None
    source_size_bytes: Optional[int] = None
    returncode: Optional[int] = None
