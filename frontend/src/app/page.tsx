"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DMMGraph } from "@/components/DMMGraph";
import { VoltageStageConfigurator, VoltageStage } from "@/components/VoltageStageConfigurator";
import { RelayStageConfigurator, RelayStage } from "@/components/RelayStageConfigurator";
import {
  MokuWaveformGeneratorConfigurator,
  MokuWaveformGeneratorStage,
  MokuWaveform,
} from "@/components/MokuWaveformGeneratorConfigurator";
import {
  Download,
  Loader2,
  Play,
  Square,
  Video,
  VideoOff,
  AlertCircle,
  Save,
  Upload,
} from "lucide-react";
import { evaluateExpression } from "@/lib/expression";

interface DMMReading {
  time: number;
  dmm1_voltage: number | null;
  dmm2_voltage: number | null;
  sample_index?: number | null;
  read_duration_ms?: number | null;
  loop_duration_ms?: number | null;
  late_by_ms?: number | null;
  overrun?: boolean;
}

type ControlSource = "ui" | "api" | "agent" | "script";
type DmmAcquisitionMode = "fast" | "low_noise";
type MeasurementSource = "dmm" | "oscilloscope" | "moku";

interface MeasurementConfig {
  test_name: string;
  measurement_source?: MeasurementSource;
  dmm1_visa_id: string | null;
  dmm2_visa_id: string | null;
  oscilloscope_visa_id?: string | null;
  moku_address?: string | null;
  power_supply_visa_id: string | null;
  relay_port: string | null;
  voltage_stages: Array<{ start_time: number; end_time: number; voltage: number }>;
  relay_ch1_stages: RelayStage[];
  relay_ch2_stages: RelayStage[];
  moku_waveform_generator_stages?: MokuWaveformGeneratorStage[];
  sampling_rate_hz: number;
  moku_sample_rate_hz?: number;
  dmm_acquisition_mode?: DmmAcquisitionMode;
  stop_after_seconds?: number | null;
  record_camera: boolean;
  auto_download_camera_recording: boolean;
  camera_ready_delay_seconds: number;
}

interface RuntimeEvent {
  timestamp: string;
  message: string;
  kind: string;
  source?: ControlSource | null;
  elapsed_time?: number | null;
}

interface SystemStatus {
  is_measuring: boolean;
  is_stopping?: boolean;
  camera_recording: boolean;
  camera_available: boolean;
  session_id: string | null;
  elapsed_time?: number | null;
  active_config?: MeasurementConfig | null;
  control_source?: ControlSource | null;
  events?: RuntimeEvent[];
  acquisition?: {
    read_duration_ms?: number | null;
    last_read_duration_ms?: number | null;
    loop_duration_ms?: number | null;
    last_loop_duration_ms?: number | null;
    overrun?: boolean;
  };
}

interface VisaResourceOption {
  resource: string;
  label: string;
  idn?: string | null;
  kind?: string;
}

interface CameraDownloadStatus {
  is_running: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  success?: boolean | null;
  message: string;
  session_dir?: string | null;
  camera_file?: string | null;
  raw_destination?: string | null;
  destination?: string | null;
  raw_metadata_path?: string | null;
  metadata_path?: string | null;
  source_size_bytes?: number | null;
  returncode?: number | null;
}

interface SaveExperimentConfigResponse {
  success: boolean;
  file_name: string;
  path: string;
  message: string;
}

const MAX_DATA_POINTS = 6000; // WebSocket updates at 10 Hz, so this keeps about 10 minutes visible.

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readOptionalString = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

const readNumber = (value: unknown, fallback: number, fieldName: string): number => {
  const numberValue =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : fallback;

  if (!Number.isFinite(numberValue)) {
    throw new Error(`${fieldName} must be a number.`);
  }

  return numberValue;
};

const readBoolean = (value: unknown, fallback: boolean, fieldName: string): boolean => {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") return true;
    if (normalized === "false") return false;
  }

  throw new Error(`${fieldName} must be true or false.`);
};

const normalizeVoltageStages = (
  value: unknown
): MeasurementConfig["voltage_stages"] => {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("voltage_stages must be an array.");

  return value.map((stage, index) => {
    if (!isRecord(stage)) {
      throw new Error(`voltage_stages[${index}] must be an object.`);
    }

    return {
      start_time: readNumber(stage.start_time, 0, `voltage_stages[${index}].start_time`),
      end_time: readNumber(stage.end_time, 0, `voltage_stages[${index}].end_time`),
      voltage: readNumber(stage.voltage, 0, `voltage_stages[${index}].voltage`),
    };
  });
};

const normalizeRelayStages = (value: unknown, fieldName: string): RelayStage[] => {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error(`${fieldName} must be an array.`);

  return value.map((stage, index) => {
    if (!isRecord(stage)) {
      throw new Error(`${fieldName}[${index}] must be an object.`);
    }

    if (stage.state !== "open" && stage.state !== "closed") {
      throw new Error(`${fieldName}[${index}].state must be open or closed.`);
    }

    return {
      start_time: readNumber(stage.start_time, 0, `${fieldName}[${index}].start_time`),
      end_time: readNumber(stage.end_time, 0, `${fieldName}[${index}].end_time`),
      state: stage.state,
    };
  });
};

const normalizeMokuWaveformGeneratorStages = (
  value: unknown
): MokuWaveformGeneratorStage[] => {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    throw new Error("moku_waveform_generator_stages must be an array.");
  }

  return value.map((stage, index) => {
    if (!isRecord(stage)) {
      throw new Error(`moku_waveform_generator_stages[${index}] must be an object.`);
    }

    if (
      stage.waveform !== "Sine" &&
      stage.waveform !== "Square" &&
      stage.waveform !== "Ramp" &&
      stage.waveform !== "Pulse"
    ) {
      throw new Error(
        `moku_waveform_generator_stages[${index}].waveform must be Sine, Square, Ramp, or Pulse.`
      );
    }

    return {
      start_time: readNumber(
        stage.start_time,
        0,
        `moku_waveform_generator_stages[${index}].start_time`
      ),
      end_time: readNumber(
        stage.end_time,
        0,
        `moku_waveform_generator_stages[${index}].end_time`
      ),
      waveform: stage.waveform as MokuWaveform,
      vpp: readNumber(stage.vpp, 0, `moku_waveform_generator_stages[${index}].vpp`),
      frequency_hz: readNumber(
        stage.frequency_hz,
        1,
        `moku_waveform_generator_stages[${index}].frequency_hz`
      ),
    };
  });
};

const normalizeLoadedConfig = (value: unknown): MeasurementConfig => {
  const configValue = isRecord(value) && "config" in value ? value.config : value;
  if (!isRecord(configValue)) {
    throw new Error("JSON file must contain a measurement config object.");
  }

  const dmmAcquisitionMode =
    configValue.dmm_acquisition_mode === "low_noise" ? "low_noise" : "fast";
  const measurementSource =
    configValue.measurement_source === "dmm"
      ? "dmm"
      : configValue.measurement_source === "moku"
        ? "moku"
        : "oscilloscope";
  const stopAfterSeconds =
    configValue.stop_after_seconds === undefined || configValue.stop_after_seconds === null
      ? null
      : readNumber(configValue.stop_after_seconds, 0, "stop_after_seconds");

  return {
    test_name:
      typeof configValue.test_name === "string" && configValue.test_name.trim()
        ? configValue.test_name
        : "test",
    measurement_source: measurementSource,
    dmm1_visa_id: readOptionalString(configValue.dmm1_visa_id),
    dmm2_visa_id: readOptionalString(configValue.dmm2_visa_id),
    oscilloscope_visa_id: readOptionalString(configValue.oscilloscope_visa_id),
    moku_address: readOptionalString(configValue.moku_address),
    power_supply_visa_id: readOptionalString(configValue.power_supply_visa_id),
    relay_port: readOptionalString(configValue.relay_port),
    voltage_stages: normalizeVoltageStages(configValue.voltage_stages),
    relay_ch1_stages: normalizeRelayStages(configValue.relay_ch1_stages, "relay_ch1_stages"),
    relay_ch2_stages: normalizeRelayStages(configValue.relay_ch2_stages, "relay_ch2_stages"),
    moku_waveform_generator_stages: normalizeMokuWaveformGeneratorStages(
      configValue.moku_waveform_generator_stages ?? configValue.moku_signal_generator_stages
    ),
    sampling_rate_hz: readNumber(configValue.sampling_rate_hz, 10, "sampling_rate_hz"),
    moku_sample_rate_hz: readNumber(
      configValue.moku_sample_rate_hz,
      10000,
      "moku_sample_rate_hz"
    ),
    dmm_acquisition_mode: dmmAcquisitionMode,
    stop_after_seconds: stopAfterSeconds,
    record_camera: readBoolean(configValue.record_camera, false, "record_camera"),
    auto_download_camera_recording: readBoolean(
      configValue.auto_download_camera_recording,
      false,
      "auto_download_camera_recording"
    ),
    camera_ready_delay_seconds: readNumber(
      configValue.camera_ready_delay_seconds,
      1,
      "camera_ready_delay_seconds"
    ),
  };
};

interface VisaSelectControlProps {
  id: string;
  label: string;
  value: string;
  resources: VisaResourceOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
}

function VisaSelectControl({
  id,
  label,
  value,
  resources,
  onChange,
  disabled = false,
}: VisaSelectControlProps) {
  const selectResources =
    value && !resources.some((resource) => resource.resource === value)
      ? [{ resource: value, label: value }, ...resources]
      : resources;

  return (
    <div className="min-w-0 space-y-1.5">
      <Label
        htmlFor={id}
        className="text-xs uppercase tracking-wide text-muted-foreground"
      >
        {label}
      </Label>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger id={id} className="h-9">
          <SelectValue placeholder="Select instrument" />
        </SelectTrigger>
        <SelectContent className="max-w-[calc(100vw-2rem)]">
          {selectResources.length === 0 ? (
            <SelectItem value="none" disabled>
              No instruments found
            </SelectItem>
          ) : (
            selectResources.map((visa) => (
              <SelectItem key={visa.resource} value={visa.resource}>
                {visa.label}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );
}

interface MeasurementSourceOptionProps {
  id: string;
  label: string;
  value: MeasurementSource;
  selectedValue: MeasurementSource;
  onChange: (value: MeasurementSource) => void;
  disabled?: boolean;
}

function MeasurementSourceOption({
  id,
  label,
  value,
  selectedValue,
  onChange,
  disabled = false,
}: MeasurementSourceOptionProps) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-border px-3 text-sm font-medium has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
    >
      <input
        id={id}
        type="radio"
        name="measurement-source"
        value={value}
        checked={selectedValue === value}
        disabled={disabled}
        onChange={() => onChange(value)}
        className="h-4 w-4 accent-primary"
      />
      {label}
    </label>
  );
}

export default function Home() {
  // State for measurements
  const [isMeasuring, setIsMeasuring] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [serverStopping, setServerStopping] = useState(false);
  const [cameraStatus, setCameraStatus] = useState({ recording: false, available: false });
  const [cameraDownloadStatus, setCameraDownloadStatus] =
    useState<CameraDownloadStatus | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [controlSource, setControlSource] = useState<ControlSource | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<RuntimeEvent[]>([]);
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const [latestTiming, setLatestTiming] = useState<{
    readDurationMs?: number | null;
    loopDurationMs?: number | null;
    overrun?: boolean;
  }>({});

  // State for instruments
  const [dmmResources, setDmmResources] = useState<VisaResourceOption[]>([]);
  const [oscilloscopeResources, setOscilloscopeResources] = useState<
    VisaResourceOption[]
  >([]);
  const [mokuResources, setMokuResources] = useState<VisaResourceOption[]>([]);
  const [powerSupplyResources, setPowerSupplyResources] = useState<VisaResourceOption[]>([]);
  const [serialPorts, setSerialPorts] = useState<string[]>([]);

  // State for voltage acquisition configuration
  const [measurementSource, setMeasurementSource] =
    useState<MeasurementSource>("oscilloscope");
  const [dmm1Visa, setDmm1Visa] = useState("");
  const [dmm2Visa, setDmm2Visa] = useState("");
  const [oscilloscopeVisa, setOscilloscopeVisa] = useState("");
  const [mokuAddress, setMokuAddress] = useState("");
  const [dmm1Data, setDmm1Data] = useState<Array<{ time: number; voltage: number }>>([]);
  const [dmm2Data, setDmm2Data] = useState<Array<{ time: number; voltage: number }>>([]);

  // State for power supply
  const [powerSupplyVisa, setPowerSupplyVisa] = useState("");
  const [voltageStages, setVoltageStages] = useState<VoltageStage[]>([]);

  // State for relay
  const [relayPort, setRelayPort] = useState("");
  const [relayCh1Stages, setRelayCh1Stages] = useState<RelayStage[]>([]);
  const [relayCh2Stages, setRelayCh2Stages] = useState<RelayStage[]>([]);
  const [mokuWaveformGeneratorStages, setMokuWaveformGeneratorStages] = useState<
    MokuWaveformGeneratorStage[]
  >([]);

  // State for test configuration
  const [testName, setTestName] = useState("test");
  const [samplingRate, setSamplingRate] = useState(10);
  const [mokuSampleRate, setMokuSampleRate] = useState(10000);
  const [dmmAcquisitionMode, setDmmAcquisitionMode] =
    useState<DmmAcquisitionMode>("fast");
  const [stopAtEnabled, setStopAtEnabled] = useState(false);
  const [stopAfterSeconds, setStopAfterSeconds] = useState(750);
  const [recordCamera, setRecordCamera] = useState(false);
  const [autoDownloadCameraRecording, setAutoDownloadCameraRecording] = useState(false);
  const [cameraReadyDelaySeconds, setCameraReadyDelaySeconds] = useState(1);
  const [loadedConfigName, setLoadedConfigName] = useState<string | null>(null);
  const [configLoadError, setConfigLoadError] = useState<string | null>(null);
  const [isSavingConfig, setIsSavingConfig] = useState(false);

  // WebSocket reference
  const wsRef = useRef<WebSocket | null>(null);
  const isMeasuringRef = useRef(false);
  const syncedSessionRef = useRef<string | null>(null);
  const configFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    isMeasuringRef.current = isMeasuring;
  }, [isMeasuring]);

  const fetchInstruments = useCallback(async () => {
    try {
      const response = await fetch("/api/list_instruments");
      const data = await response.json();

      const detailMap = new Map<string, VisaResourceOption>(
        (data.visa_details || []).map((detail: VisaResourceOption) => [
          detail.resource,
          {
            resource: detail.resource,
            label: detail.label || detail.resource,
            idn: detail.idn,
            kind: detail.kind,
          },
        ])
      );
      const toOptions = (resources: string[]): VisaResourceOption[] =>
        resources.map((resource) => detailMap.get(resource) || { resource, label: resource });
      const nextDmmResources = toOptions(data.dmm_resources || data.visa_resources || []);
      const nextOscilloscopeResources = toOptions(
        data.oscilloscope_resources || data.visa_resources || []
      );
      const nextMokuResources = toOptions(data.moku_resources || []);
      const nextPowerSupplyResources = toOptions(
        data.power_supply_resources || data.visa_resources || []
      );

      setDmmResources(nextDmmResources);
      setOscilloscopeResources(nextOscilloscopeResources);
      setMokuResources(nextMokuResources);
      setPowerSupplyResources(nextPowerSupplyResources);
      setSerialPorts(data.serial_ports || []);
      setDmm1Visa((value) =>
        value && !nextDmmResources.some((option) => option.resource === value) ? "" : value
      );
      setDmm2Visa((value) =>
        value && !nextDmmResources.some((option) => option.resource === value) ? "" : value
      );
      setOscilloscopeVisa((value) =>
        value && !nextOscilloscopeResources.some((option) => option.resource === value)
          ? ""
          : value
      );
      setMokuAddress((value) => value);
      setPowerSupplyVisa((value) =>
        value && !nextPowerSupplyResources.some((option) => option.resource === value)
          ? ""
          : value
      );
    } catch (error) {
      console.error("Failed to fetch instruments:", error);
    }
  }, []);

  const applyMeasurementConfig = useCallback((config: MeasurementConfig) => {
    setTestName(config.test_name || "test");
    setMeasurementSource(config.measurement_source || "oscilloscope");
    setDmm1Visa(config.dmm1_visa_id || "");
    setDmm2Visa(config.dmm2_visa_id || "");
    setOscilloscopeVisa(config.oscilloscope_visa_id || "");
    setMokuAddress(config.moku_address || "");
    setPowerSupplyVisa(config.power_supply_visa_id || "");
    setRelayPort(config.relay_port || "");
    setVoltageStages(
      (config.voltage_stages || []).map((stage) => ({
        ...stage,
        voltageExpression: String(stage.voltage),
        voltageExpressionError: undefined,
      }))
    );
    setRelayCh1Stages(config.relay_ch1_stages || []);
    setRelayCh2Stages(config.relay_ch2_stages || []);
    setMokuWaveformGeneratorStages(config.moku_waveform_generator_stages || []);
    setSamplingRate(config.sampling_rate_hz || 10);
    setMokuSampleRate(config.moku_sample_rate_hz || 10000);
    setDmmAcquisitionMode(config.dmm_acquisition_mode || "fast");
    setStopAtEnabled(
      typeof config.stop_after_seconds === "number" &&
        Number.isFinite(config.stop_after_seconds)
    );
    setStopAfterSeconds(config.stop_after_seconds ?? 750);
    setRecordCamera(Boolean(config.record_camera));
    setAutoDownloadCameraRecording(Boolean(config.auto_download_camera_recording));
    setCameraReadyDelaySeconds(config.camera_ready_delay_seconds ?? 0);
  }, []);

  const handleConfigFileChange = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file || isMeasuringRef.current) return;

      try {
        const parsed = JSON.parse(await file.text());
        const config = normalizeLoadedConfig(parsed);
        applyMeasurementConfig(config);
        setLoadedConfigName(file.name);
        setConfigLoadError(null);
      } catch (error) {
        setLoadedConfigName(null);
        setConfigLoadError(
          error instanceof Error ? error.message : "Failed to load config file."
        );
      }
    },
    [applyMeasurementConfig]
  );

  const fetchCameraDownloadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/download_latest_camera_recording/status");
      if (!response.ok) return;
      const data: CameraDownloadStatus = await response.json();
      setCameraDownloadStatus(data);
    } catch (error) {
      console.error("Failed to fetch camera download status:", error);
    }
  }, []);

  const handleDownloadLatestCameraRecording = useCallback(async () => {
    try {
      const response = await fetch("/api/download_latest_camera_recording", {
        method: "POST",
      });
      const data = await response.json();

      if (!response.ok) {
        const message = data.detail || "Failed to start camera recording download.";
        setCameraDownloadStatus({
          is_running: false,
          success: false,
          message,
        });
        alert(message);
        return;
      }

      setCameraDownloadStatus(data);
    } catch (error) {
      const message = "Failed to start camera recording download.";
      setCameraDownloadStatus({
        is_running: false,
        success: false,
        message,
      });
      console.error("Error starting camera recording download:", error);
      alert(message);
    }
  }, []);

  const replaceLiveData = useCallback((readings: DMMReading[]) => {
    const dmm1 = readings
      .filter((reading) => reading.time !== null && reading.dmm1_voltage !== null)
      .map((reading) => ({ time: reading.time, voltage: reading.dmm1_voltage! }))
      .slice(-MAX_DATA_POINTS);
    const dmm2 = readings
      .filter((reading) => reading.time !== null && reading.dmm2_voltage !== null)
      .map((reading) => ({ time: reading.time, voltage: reading.dmm2_voltage! }))
      .slice(-MAX_DATA_POINTS);

    setDmm1Data(dmm1);
    setDmm2Data(dmm2);

    const latest = readings[readings.length - 1];
    if (latest?.time !== null && latest?.time !== undefined) {
      setElapsedTime(latest.time);
      setLatestTiming({
        readDurationMs: latest.read_duration_ms,
        loopDurationMs: latest.loop_duration_ms,
        overrun: latest.overrun,
      });
    }
  }, []);

  const loadCurrentSessionData = useCallback(async () => {
    try {
      const response = await fetch(`/api/current_session/data?limit=${MAX_DATA_POINTS}`);
      if (!response.ok) return;
      const data = await response.json();
      replaceLiveData(data.data || []);
    } catch (error) {
      console.error("Failed to fetch current session data:", error);
    }
  }, [replaceLiveData]);

  const appendReading = useCallback((reading: DMMReading) => {
    if (reading.time === null || reading.time === undefined) return;

    if (reading.dmm1_voltage !== null) {
      setDmm1Data((prev) => {
        if (prev[prev.length - 1]?.time === reading.time) return prev;
        return [...prev, { time: reading.time, voltage: reading.dmm1_voltage! }].slice(
          -MAX_DATA_POINTS
        );
      });
    }

    if (reading.dmm2_voltage !== null) {
      setDmm2Data((prev) => {
        if (prev[prev.length - 1]?.time === reading.time) return prev;
        return [...prev, { time: reading.time, voltage: reading.dmm2_voltage! }].slice(
          -MAX_DATA_POINTS
        );
      });
    }

    setElapsedTime(reading.time);
    setLatestTiming({
      readDurationMs: reading.read_duration_ms,
      loopDurationMs: reading.loop_duration_ms,
      overrun: reading.overrun,
    });
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/status");
      const data: SystemStatus = await response.json();
      const nextSessionId = data.session_id || null;

      setIsMeasuring(data.is_measuring);
      setServerStopping(Boolean(data.is_stopping));
      setCameraStatus({
        recording: data.camera_recording,
        available: data.camera_available,
      });
      setSessionId(nextSessionId);
      setControlSource(data.control_source || null);
      setRuntimeEvents(data.events || []);
      setElapsedTime(data.elapsed_time || 0);
      setLatestTiming((prev) => ({
        readDurationMs: data.acquisition?.last_read_duration_ms ?? prev.readDurationMs,
        loopDurationMs: data.acquisition?.last_loop_duration_ms ?? prev.loopDurationMs,
        overrun: data.acquisition?.overrun ?? prev.overrun,
      }));

      if (data.is_measuring && data.active_config && nextSessionId) {
        if (syncedSessionRef.current !== nextSessionId) {
          applyMeasurementConfig(data.active_config);
          await loadCurrentSessionData();
          syncedSessionRef.current = nextSessionId;
        }
      } else if (!data.is_measuring && syncedSessionRef.current && !nextSessionId) {
        syncedSessionRef.current = null;
      }
    } catch (error) {
      console.error("Failed to fetch status:", error);
    }
  }, [applyMeasurementConfig, loadCurrentSessionData]);

  useEffect(() => {
    fetchInstruments();
    fetchStatus();
    fetchCameraDownloadStatus();

    const statusTimer = window.setInterval(fetchStatus, 1000);
    return () => window.clearInterval(statusTimer);
  }, [fetchInstruments, fetchStatus, fetchCameraDownloadStatus]);

  useEffect(() => {
    const pollMs = cameraDownloadStatus?.is_running ? 2000 : 5000;
    const downloadTimer = window.setInterval(fetchCameraDownloadStatus, pollMs);
    return () => window.clearInterval(downloadTimer);
  }, [cameraDownloadStatus?.is_running, fetchCameraDownloadStatus]);

  // WebSocket connection
  const connectWebSocket = useCallback(() => {
    const ws = new WebSocket("ws://localhost:8000/api/live");

    ws.onopen = () => {
      console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
      try {
        const reading: DMMReading = JSON.parse(event.data);

        appendReading(reading);
      } catch (error) {
        console.error("Error parsing WebSocket message:", error);
      }
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    ws.onclose = () => {
      console.log("WebSocket disconnected");
      // Reconnect if measuring
      if (isMeasuringRef.current) {
        setTimeout(connectWebSocket, 1000);
      }
    };

    wsRef.current = ws;
  }, [appendReading]);

  const disconnectWebSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  // Connect/disconnect WebSocket based on measurement state
  useEffect(() => {
    if (isMeasuring) {
      connectWebSocket();
    } else {
      disconnectWebSocket();
    }

    return () => {
      disconnectWebSocket();
    };
  }, [isMeasuring, connectWebSocket, disconnectWebSocket]);

  const buildCurrentMeasurementConfig = useCallback((): MeasurementConfig | null => {
    if (measurementSource === "dmm" && dmm1Visa && dmm2Visa && dmm1Visa === dmm2Visa) {
      alert("DMM1 and DMM2 must use different VISA IDs.");
      return null;
    }

    if (measurementSource === "oscilloscope" && !oscilloscopeVisa) {
      alert("Add an oscilloscope VISA ID before using oscilloscope mode.");
      return null;
    }

    if (measurementSource === "moku" && !mokuAddress) {
      alert("Add a Moku:Pro address before using Moku mode.");
      return null;
    }

    if (
      measurementSource === "moku" &&
      (!Number.isFinite(mokuSampleRate) || mokuSampleRate < 10)
    ) {
      alert("Moku:Pro sample rate must be at least 10 Hz.");
      return null;
    }
    if (measurementSource === "moku" && mokuSampleRate > 1000000) {
      alert("Moku:Pro API logging sample rate must be at most 1 MSa/s.");
      return null;
    }

    if (voltageStages.length > 0 && !powerSupplyVisa) {
      alert("Add a power supply VISA ID before using power supply stages.");
      return null;
    }

    if ((relayCh1Stages.length > 0 || relayCh2Stages.length > 0) && !relayPort) {
      alert("Add a relay board serial port before using relay stages.");
      return null;
    }

    if (stopAtEnabled && (!Number.isFinite(stopAfterSeconds) || stopAfterSeconds <= 0)) {
      alert("Stop time must be greater than 0 seconds.");
      return null;
    }

    const normalizedVoltageStages: MeasurementConfig["voltage_stages"] = [];
    const normalizedMokuWaveformGeneratorStages: MokuWaveformGeneratorStage[] = [];

    for (let i = 0; i < voltageStages.length; i++) {
      const stage = voltageStages[i];
      if (stage.end_time <= stage.start_time) {
        alert(`Power stage ${i + 1}: end time must be after start time.`);
        return null;
      }

      const expression = stage.voltageExpression ?? String(stage.voltage);
      const evaluation = evaluateExpression(expression, { t: stage.start_time });

      if (evaluation.error || evaluation.value === null) {
        alert(`Power stage ${i + 1}: ${evaluation.error ?? "Invalid expression"}`);
        return null;
      }

      normalizedVoltageStages.push({
        start_time: stage.start_time,
        end_time: stage.end_time,
        voltage: evaluation.value,
      });
    }

    if (measurementSource === "moku") {
      for (let i = 0; i < mokuWaveformGeneratorStages.length; i++) {
        const stage = mokuWaveformGeneratorStages[i];
        if (stage.end_time <= stage.start_time) {
          alert(`Moku waveform generator stage ${i + 1}: end time must be after start time.`);
          return null;
        }
        if (!Number.isFinite(stage.vpp) || stage.vpp < 0) {
          alert(`Moku waveform generator stage ${i + 1}: Vpp must be 0 or greater.`);
          return null;
        }
        if (!Number.isFinite(stage.frequency_hz) || stage.frequency_hz <= 0) {
          alert(
            `Moku waveform generator stage ${i + 1}: frequency must be greater than 0 Hz.`
          );
          return null;
        }

        normalizedMokuWaveformGeneratorStages.push({
          start_time: stage.start_time,
          end_time: stage.end_time,
          waveform: stage.waveform,
          vpp: stage.vpp,
          frequency_hz: stage.frequency_hz,
        });
      }
    }

    for (const [channel, stages] of [
      [1, relayCh1Stages],
      [2, relayCh2Stages],
    ] as const) {
      for (let i = 0; i < stages.length; i++) {
        const stage = stages[i];
        if (stage.end_time <= stage.start_time) {
          alert(`Relay CH${channel} stage ${i + 1}: end time must be after start time.`);
          return null;
        }
      }
    }

    setVoltageStages((prev) =>
      prev.map((stage, index) => ({
        ...stage,
        voltage: normalizedVoltageStages[index]?.voltage ?? stage.voltage,
        voltageExpressionError: undefined,
      }))
    );

    return {
      test_name: testName,
      measurement_source: measurementSource,
      dmm1_visa_id: dmm1Visa || null,
      dmm2_visa_id: dmm2Visa || null,
      oscilloscope_visa_id: oscilloscopeVisa || null,
      moku_address: mokuAddress || null,
      power_supply_visa_id: powerSupplyVisa || null,
      relay_port: relayPort || null,
      voltage_stages: normalizedVoltageStages,
      relay_ch1_stages: relayCh1Stages,
      relay_ch2_stages: relayCh2Stages,
      moku_waveform_generator_stages:
        measurementSource === "moku" ? normalizedMokuWaveformGeneratorStages : [],
      sampling_rate_hz: measurementSource === "moku" ? 10 : samplingRate,
      moku_sample_rate_hz: mokuSampleRate,
      dmm_acquisition_mode: dmmAcquisitionMode,
      stop_after_seconds: stopAtEnabled ? stopAfterSeconds : null,
      record_camera: recordCamera,
      auto_download_camera_recording: recordCamera && autoDownloadCameraRecording,
      camera_ready_delay_seconds: cameraReadyDelaySeconds,
    };
  }, [
    autoDownloadCameraRecording,
    cameraReadyDelaySeconds,
    dmm1Visa,
    dmm2Visa,
    dmmAcquisitionMode,
    measurementSource,
    mokuAddress,
    mokuSampleRate,
    mokuWaveformGeneratorStages,
    oscilloscopeVisa,
    powerSupplyVisa,
    recordCamera,
    relayCh1Stages,
    relayCh2Stages,
    relayPort,
    samplingRate,
    stopAfterSeconds,
    stopAtEnabled,
    testName,
    voltageStages,
  ]);

  const handleStartMeasurement = async () => {
    try {
      const config = buildCurrentMeasurementConfig();
      if (!config) return;

      setIsStarting(true);

      const response = await fetch("/api/start_measurement", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config, control_source: "ui" }),
      });

      if (!response.ok) {
        const error = await response.json();
        alert(`Failed to start measurement: ${error.detail}`);
        setIsStarting(false);
        return;
      }

      const data = await response.json();
      setSessionId(data.session_id);
      setControlSource("ui");
      setIsMeasuring(true);
      setIsStarting(false);

      // Clear previous data
      setDmm1Data([]);
      setDmm2Data([]);
      setElapsedTime(0);
      setLatestTiming({});

      console.log("Measurement started:", data);
    } catch (error) {
      setIsStarting(false);
      console.error("Error starting measurement:", error);
      alert("Failed to start measurement. Check console for details.");
    }
  };

  const handleSaveConfig = async () => {
    const config = buildCurrentMeasurementConfig();
    if (!config) return;

    setIsSavingConfig(true);
    setConfigLoadError(null);

    try {
      const response = await fetch("/api/experiment_configs/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });
      const data = await response.json();

      if (!response.ok) {
        const message = data.detail || "Failed to save config.";
        setConfigLoadError(message);
        alert(message);
        return;
      }

      const savedConfig = data as SaveExperimentConfigResponse;
      setLoadedConfigName(savedConfig.file_name);
      setConfigLoadError(null);
    } catch (error) {
      const message = "Failed to save config.";
      setConfigLoadError(message);
      console.error("Error saving config:", error);
      alert(message);
    } finally {
      setIsSavingConfig(false);
    }
  };

  const handleStopMeasurement = async () => {
    // The stop sequence can take a while (e.g. Moku downloads/converts its
    // waveform off the device), so guard against duplicate clicks that would
    // otherwise hit the backend "already stopping" error.
    if (isStopping || serverStopping) return;
    setIsStopping(true);
    try {
      const response = await fetch("/api/stop_measurement?control_source=ui", {
        method: "POST",
      });

      if (!response.ok) {
        const error = await response.json();
        alert(`Failed to stop measurement: ${error.detail}`);
        return;
      }

      const data = await response.json();
      if (data.status && data.status !== "stopped") {
        // A stop is already underway (or nothing was running); let status
        // polling reconcile the UI instead of reporting a failure.
        console.log("Stop no-op:", data.status);
        return;
      }
      setIsMeasuring(false);
      setSessionId(null);

      console.log("Measurement stopped:", data);
      const savedPaths = [`Measurement saved to:`, data.csv_path];
      if (data.oscilloscope_csv_path) {
        savedPaths.push(`Oscilloscope waveform:`, data.oscilloscope_csv_path);
      }
      if (data.moku_csv_path) {
        savedPaths.push(`Moku:Pro waveform:`, data.moku_csv_path);
      }
      alert(savedPaths.join("\n"));
    } catch (error) {
      console.error("Error stopping measurement:", error);
      alert("Failed to stop measurement. Check console for details.");
    } finally {
      setIsStopping(false);
    }
  };

  // Combine the optimistic local stop (instant button feedback) with the
  // backend's is_stopping flag (so the state survives a mid-stop page reload).
  const stopping = isStopping || serverStopping;
  const controlSourceLabel =
    controlSource === "ui"
      ? "Human UI"
      : controlSource === "agent"
        ? "AI Agent"
        : controlSource === "script"
          ? "Script"
          : controlSource === "api"
            ? "API"
            : "Idle";
  const isCameraDownloadRunning = Boolean(cameraDownloadStatus?.is_running);
  const cameraDownloadMessage = cameraDownloadStatus?.destination
    ? `${cameraDownloadStatus.message}: ${cameraDownloadStatus.destination}`
    : cameraDownloadStatus?.message || "No transfer started";
  const showCameraDownloadMessage = Boolean(
    cameraDownloadStatus &&
      (cameraDownloadStatus.is_running ||
        (cameraDownloadStatus.success !== null && cameraDownloadStatus.success !== undefined) ||
        cameraDownloadStatus.destination ||
        cameraDownloadStatus.raw_destination)
  );
  const cameraDownloadDisabled =
    isMeasuring || isStarting || cameraStatus.recording || isCameraDownloadRunning;
  const firstTraceTitle = measurementSource === "dmm" ? "DMM 1" : "CH1 voltage";
  const secondTraceTitle = measurementSource === "dmm" ? "DMM 2" : "CH2 voltage";

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0">
            <h1 className="whitespace-nowrap text-2xl font-bold leading-tight">
              ECA Testing Webapp
            </h1>
            <p className="text-sm text-muted-foreground">
              Electrochemical Actuator Testing and Control
            </p>
          </div>
          <div className="grid w-full gap-3 sm:w-auto sm:grid-cols-[auto_8rem_5rem_auto_auto] sm:items-end sm:justify-start">
            <label
              htmlFor="header-record-camera"
              className="flex h-10 min-w-0 cursor-pointer items-center gap-2 rounded-md border border-border px-3 text-sm font-medium has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
            >
              <input
                id="header-record-camera"
                type="checkbox"
                checked={recordCamera}
                onChange={(event) => setRecordCamera(event.target.checked)}
                disabled={isMeasuring || isStarting}
                className="h-4 w-4 rounded border-input"
              />
              {cameraStatus.recording ? (
                <Video className="h-4 w-4 shrink-0 text-red-500" />
              ) : (
                <VideoOff className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate">Camera</span>
            </label>

            <div className="space-y-1">
              <Label
                htmlFor="ready-delay-seconds"
                className="text-xs uppercase tracking-wide text-muted-foreground"
              >
                Ready Delay (s)
              </Label>
              <Input
                id="ready-delay-seconds"
                type="number"
                value={cameraReadyDelaySeconds}
                onChange={(event) =>
                  setCameraReadyDelaySeconds(Math.max(0, parseFloat(event.target.value) || 0))
                }
                disabled={isMeasuring || isStarting}
                min={0}
                max={30}
                step={0.1}
                className="h-10"
              />
            </div>

            <div className="flex h-10 min-w-20 items-center text-sm font-mono text-muted-foreground">
              {isMeasuring ? `${elapsedTime.toFixed(3)} s` : null}
            </div>

            <Button
              onClick={handleStartMeasurement}
              disabled={isMeasuring || isStarting || stopping}
              size="lg"
              className="min-w-0 gap-2 px-4"
            >
              {isStarting ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              ) : (
                <Play className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate">{isStarting ? "Starting" : "Start"}</span>
            </Button>
            <Button
              onClick={handleStopMeasurement}
              disabled={!isMeasuring || isStarting || stopping}
              variant="destructive"
              size="lg"
              className="min-w-0 gap-2 px-4"
            >
              {stopping ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              ) : (
                <Square className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate">{stopping ? "Stopping" : "Stop"}</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
          {/* Left Column - Voltage Graphs */}
          <div className="min-w-0 space-y-6">
            <Card>
              <CardContent className="space-y-4 p-4">
                <fieldset className="grid gap-3 sm:grid-cols-3">
                  <legend className="sr-only">Voltage measurement source</legend>
                  <MeasurementSourceOption
                    id="measurement-source-dmm"
                    label="DMM"
                    value="dmm"
                    selectedValue={measurementSource}
                    onChange={setMeasurementSource}
                    disabled={isMeasuring || isStarting}
                  />
                  <MeasurementSourceOption
                    id="measurement-source-oscilloscope"
                    label="Oscilloscope"
                    value="oscilloscope"
                    selectedValue={measurementSource}
                    onChange={setMeasurementSource}
                    disabled={isMeasuring || isStarting}
                  />
                  <MeasurementSourceOption
                    id="measurement-source-moku"
                    label="Moku:Pro"
                    value="moku"
                    selectedValue={measurementSource}
                    onChange={setMeasurementSource}
                    disabled={isMeasuring || isStarting}
                  />
                </fieldset>

                {measurementSource === "oscilloscope" ? (
                  <VisaSelectControl
                    id="oscilloscope-visa"
                    label="Oscilloscope VISA ID"
                    value={oscilloscopeVisa}
                    resources={oscilloscopeResources}
                    onChange={setOscilloscopeVisa}
                    disabled={isMeasuring || isStarting}
                  />
                ) : measurementSource === "moku" ? (
                  <VisaSelectControl
                    id="moku-address"
                    label="Moku:Pro"
                    value={mokuAddress}
                    resources={mokuResources}
                    onChange={setMokuAddress}
                    disabled={isMeasuring || isStarting}
                  />
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <VisaSelectControl
                      id="dmm-1-visa"
                      label="DMM 1 VISA ID"
                      value={dmm1Visa}
                      resources={dmmResources}
                      onChange={setDmm1Visa}
                      disabled={isMeasuring || isStarting}
                    />
                    <VisaSelectControl
                      id="dmm-2-visa"
                      label="DMM 2 VISA ID"
                      value={dmm2Visa}
                      resources={dmmResources}
                      onChange={setDmm2Visa}
                      disabled={isMeasuring || isStarting}
                    />
                  </div>
                )}
                {measurementSource === "dmm" || measurementSource === "moku" ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label
                        htmlFor="sampling-rate"
                        className="text-xs uppercase tracking-wide text-muted-foreground"
                      >
                        {measurementSource === "moku" ? "Moku Rate (Hz)" : "Sample Rate (Hz)"}
                      </Label>
                      <Input
                        id="sampling-rate"
                        type="number"
                        value={measurementSource === "moku" ? mokuSampleRate : samplingRate}
                        onChange={(e) => {
                          const value = parseFloat(e.target.value) || 10;
                          if (measurementSource === "moku") {
                            setMokuSampleRate(value);
                          } else {
                            setSamplingRate(value);
                          }
                        }}
                        disabled={isMeasuring || isStarting}
                        min={measurementSource === "moku" ? 10 : 1}
                        max={measurementSource === "moku" ? 1000000 : 300}
                        className="h-9"
                      />
                    </div>
                    {measurementSource === "dmm" ? (
                      <div className="space-y-1.5">
                        <Label
                          htmlFor="dmm-acquisition-mode"
                          className="text-xs uppercase tracking-wide text-muted-foreground"
                        >
                          DMM Mode
                        </Label>
                        <Select
                          value={dmmAcquisitionMode}
                          onValueChange={(value) =>
                            setDmmAcquisitionMode(value as DmmAcquisitionMode)
                          }
                          disabled={isMeasuring || isStarting}
                        >
                          <SelectTrigger id="dmm-acquisition-mode" className="h-9">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="fast">Fast (0.02 PLC)</SelectItem>
                            <SelectItem value="low_noise">Low noise (1 PLC)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {measurementSource === "moku" ? (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium uppercase leading-none tracking-wide text-muted-foreground">
                      Waveform Generator
                    </p>
                    <MokuWaveformGeneratorConfigurator
                      stages={mokuWaveformGeneratorStages}
                      onStagesChange={setMokuWaveformGeneratorStages}
                      disabled={isMeasuring || isStarting}
                    />
                  </div>
                ) : null}
              </CardContent>
            </Card>
            <DMMGraph
              title={firstTraceTitle}
              data={dmm1Data}
            />
            <DMMGraph
              title={secondTraceTitle}
              data={dmm2Data}
            />
          </div>

          {/* Right Column - Controls */}
          <div className="min-w-0 space-y-6">
            <Card>
              <CardContent className="pt-6">
                <div className="grid gap-4">
                  <div className="space-y-1.5">
                    <Label
                      htmlFor="test-name"
                      className="text-xs uppercase tracking-wide text-muted-foreground"
                    >
                      Test Name
                    </Label>
                    <Input
                      id="test-name"
                      value={testName}
                      onChange={(e) => setTestName(e.target.value)}
                      disabled={isMeasuring || isStarting}
                      placeholder="test"
                      className="h-9"
                    />
                  </div>
                </div>
                <input
                  ref={configFileInputRef}
                  type="file"
                  accept="application/json,.json"
                  onChange={handleConfigFileChange}
                  disabled={isMeasuring || isStarting}
                  className="hidden"
                />
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleSaveConfig}
                    disabled={isMeasuring || isStarting || isSavingConfig}
                    className="gap-2"
                  >
                    {isSavingConfig ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="h-4 w-4" />
                    )}
                    {isSavingConfig ? "Saving" : "Save Config"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => configFileInputRef.current?.click()}
                    disabled={isMeasuring || isStarting || isSavingConfig}
                    className="gap-2"
                  >
                    <Upload className="h-4 w-4" />
                    Load Config
                  </Button>
                </div>
                {(configLoadError || isSavingConfig || loadedConfigName) && (
                  <div
                    className={
                      configLoadError
                        ? "mt-2 truncate text-xs text-destructive"
                        : "mt-2 truncate text-xs text-muted-foreground"
                    }
                  >
                    {configLoadError || (isSavingConfig ? "Saving..." : loadedConfigName)}
                  </div>
                )}

                <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_8rem] sm:items-center">
                  <label
                    htmlFor="stop-at-enabled"
                    className="flex min-w-0 cursor-pointer items-center gap-3 text-sm font-medium has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
                  >
                    <input
                      id="stop-at-enabled"
                      type="checkbox"
                      checked={stopAtEnabled}
                      onChange={(event) => setStopAtEnabled(event.target.checked)}
                      disabled={isMeasuring || isStarting}
                      className="h-4 w-4 rounded border-input"
                    />
                    <span className="truncate">Auto-stop at (s)</span>
                  </label>
                  <Input
                    id="stop-after-seconds"
                    type="number"
                    value={stopAfterSeconds}
                    onChange={(event) =>
                      setStopAfterSeconds(Math.max(0, parseFloat(event.target.value) || 0))
                    }
                    disabled={!stopAtEnabled || isMeasuring || isStarting}
                    min={0.1}
                    step={0.1}
                    className="h-9"
                  />
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
                  <label
                    htmlFor="auto-download-camera-recording"
                    className="flex min-w-0 cursor-pointer items-center gap-3 text-sm font-medium has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
                  >
                    <input
                      id="auto-download-camera-recording"
                      type="checkbox"
                      checked={recordCamera && autoDownloadCameraRecording}
                      onChange={(event) =>
                        setAutoDownloadCameraRecording(event.target.checked)
                      }
                      disabled={!recordCamera || isMeasuring || isStarting}
                      className="h-4 w-4 rounded border-input"
                    />
                    <span className="truncate">Auto-download</span>
                  </label>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleDownloadLatestCameraRecording}
                    disabled={cameraDownloadDisabled}
                    className="gap-2"
                  >
                    {isCameraDownloadRunning ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4" />
                    )}
                    {isCameraDownloadRunning ? "Downloading" : "Download & Compress"}
                  </Button>
                </div>
                {showCameraDownloadMessage && (
                  <div
                    className={
                      cameraDownloadStatus?.success === false
                        ? "mt-2 truncate text-xs text-destructive"
                        : "mt-2 truncate text-xs text-muted-foreground"
                    }
                  >
                    {cameraDownloadMessage}
                  </div>
                )}
              </CardContent>
            </Card>

            <VoltageStageConfigurator
              stages={voltageStages}
              onStagesChange={setVoltageStages}
              visaResources={powerSupplyResources}
              selectedVisa={powerSupplyVisa}
              onVisaChange={setPowerSupplyVisa}
              disabled={isMeasuring || isStarting}
            />

            <RelayStageConfigurator
              channel={1}
              stages={relayCh1Stages}
              onStagesChange={setRelayCh1Stages}
              serialPorts={serialPorts}
              selectedPort={relayPort}
              onPortChange={setRelayPort}
              disabled={isMeasuring || isStarting}
            />

            <RelayStageConfigurator
              channel={2}
              stages={relayCh2Stages}
              onStagesChange={setRelayCh2Stages}
              serialPorts={serialPorts}
              selectedPort={relayPort}
              onPortChange={setRelayPort}
              disabled={isMeasuring || isStarting}
              showPortSelector={false}
            />

            {/* Session Info */}
            {sessionId && (
              <Card>
                <CardHeader>
                  <CardTitle>Current Session</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm font-mono break-all">{sessionId}</p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Control: {controlSourceLabel}
                  </p>
                  {latestTiming.loopDurationMs !== undefined && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      Read {latestTiming.readDurationMs?.toFixed(1)} ms · Loop{" "}
                      {latestTiming.loopDurationMs?.toFixed(1)} ms
                      {latestTiming.overrun ? " · Overrun" : ""}
                    </p>
                  )}
                </CardContent>
              </Card>
            )}

            {runtimeEvents.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Runtime Log</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="max-h-64 space-y-2 overflow-auto text-xs">
                    {runtimeEvents.slice(-20).map((event, index) => (
                      <div
                        key={`${event.timestamp}-${index}`}
                        className="grid grid-cols-[4.5rem_1fr] gap-2 border-b border-border/60 pb-2 last:border-b-0 last:pb-0"
                      >
                        <span className="font-mono text-muted-foreground">
                          {event.elapsed_time !== null && event.elapsed_time !== undefined
                            ? `${event.elapsed_time.toFixed(3)}s`
                            : "--"}
                        </span>
                        <span
                          className={
                            event.kind === "error"
                              ? "text-destructive"
                              : event.kind === "warning"
                                ? "text-yellow-700"
                                : "text-foreground"
                          }
                        >
                          {event.message}
                        </span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
        {/* Info Banner */}
        {!cameraStatus.available && (
          <div className="mt-6">
            <Card className="border-yellow-500/50 bg-yellow-500/10">
              <CardContent className="flex items-center gap-2 pt-6">
                <AlertCircle className="h-5 w-5 text-yellow-500" />
                <p className="text-sm">
                  Camera service not available. Running in mock mode. Data logging will still work.
                </p>
              </CardContent>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
