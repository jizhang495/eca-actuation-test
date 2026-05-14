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
type MeasurementSource = "dmm" | "oscilloscope";

interface MeasurementConfig {
  test_name: string;
  measurement_source?: MeasurementSource;
  dmm1_visa_id: string | null;
  dmm2_visa_id: string | null;
  oscilloscope_visa_id?: string | null;
  power_supply_visa_id: string | null;
  relay_port: string | null;
  voltage_stages: Array<{ start_time: number; end_time: number; voltage: number }>;
  relay_ch1_stages: RelayStage[];
  relay_ch2_stages: RelayStage[];
  sampling_rate_hz: number;
  dmm_acquisition_mode?: DmmAcquisitionMode;
  stop_after_seconds?: number | null;
  record_camera: boolean;
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
  destination?: string | null;
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

const normalizeLoadedConfig = (value: unknown): MeasurementConfig => {
  const configValue = isRecord(value) && "config" in value ? value.config : value;
  if (!isRecord(configValue)) {
    throw new Error("JSON file must contain a measurement config object.");
  }

  const dmmAcquisitionMode =
    configValue.dmm_acquisition_mode === "low_noise" ? "low_noise" : "fast";
  const measurementSource =
    configValue.measurement_source === "oscilloscope" ? "oscilloscope" : "dmm";
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
    power_supply_visa_id: readOptionalString(configValue.power_supply_visa_id),
    relay_port: readOptionalString(configValue.relay_port),
    voltage_stages: normalizeVoltageStages(configValue.voltage_stages),
    relay_ch1_stages: normalizeRelayStages(configValue.relay_ch1_stages, "relay_ch1_stages"),
    relay_ch2_stages: normalizeRelayStages(configValue.relay_ch2_stages, "relay_ch2_stages"),
    sampling_rate_hz: readNumber(configValue.sampling_rate_hz, 10, "sampling_rate_hz"),
    dmm_acquisition_mode: dmmAcquisitionMode,
    stop_after_seconds: stopAfterSeconds,
    record_camera: readBoolean(configValue.record_camera, false, "record_camera"),
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
          {resources.length === 0 ? (
            <SelectItem value="none" disabled>
              No instruments found
            </SelectItem>
          ) : (
            resources.map((visa) => (
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
  const [powerSupplyResources, setPowerSupplyResources] = useState<VisaResourceOption[]>([]);
  const [serialPorts, setSerialPorts] = useState<string[]>([]);

  // State for voltage acquisition configuration
  const [measurementSource, setMeasurementSource] =
    useState<MeasurementSource>("dmm");
  const [dmm1Visa, setDmm1Visa] = useState("");
  const [dmm2Visa, setDmm2Visa] = useState("");
  const [oscilloscopeVisa, setOscilloscopeVisa] = useState("");
  const [dmm1Data, setDmm1Data] = useState<Array<{ time: number; voltage: number }>>([]);
  const [dmm2Data, setDmm2Data] = useState<Array<{ time: number; voltage: number }>>([]);

  // State for power supply
  const [powerSupplyVisa, setPowerSupplyVisa] = useState("");
  const [voltageStages, setVoltageStages] = useState<VoltageStage[]>([]);

  // State for relay
  const [relayPort, setRelayPort] = useState("");
  const [relayCh1Stages, setRelayCh1Stages] = useState<RelayStage[]>([]);
  const [relayCh2Stages, setRelayCh2Stages] = useState<RelayStage[]>([]);

  // State for test configuration
  const [testName, setTestName] = useState("test");
  const [samplingRate, setSamplingRate] = useState(10);
  const [dmmAcquisitionMode, setDmmAcquisitionMode] =
    useState<DmmAcquisitionMode>("fast");
  const [stopAtEnabled, setStopAtEnabled] = useState(false);
  const [stopAfterSeconds, setStopAfterSeconds] = useState(750);
  const [recordCamera, setRecordCamera] = useState(false);
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
      const nextPowerSupplyResources = toOptions(
        data.power_supply_resources || data.visa_resources || []
      );

      setDmmResources(nextDmmResources);
      setOscilloscopeResources(nextOscilloscopeResources);
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
    setMeasurementSource(config.measurement_source || "dmm");
    setDmm1Visa(config.dmm1_visa_id || "");
    setDmm2Visa(config.dmm2_visa_id || "");
    setOscilloscopeVisa(config.oscilloscope_visa_id || "");
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
    setSamplingRate(config.sampling_rate_hz || 10);
    setDmmAcquisitionMode(config.dmm_acquisition_mode || "fast");
    setStopAtEnabled(
      typeof config.stop_after_seconds === "number" &&
        Number.isFinite(config.stop_after_seconds)
    );
    setStopAfterSeconds(config.stop_after_seconds ?? 750);
    setRecordCamera(Boolean(config.record_camera));
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
    if (!cameraDownloadStatus?.is_running) return;

    const downloadTimer = window.setInterval(fetchCameraDownloadStatus, 2000);
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
      power_supply_visa_id: powerSupplyVisa || null,
      relay_port: relayPort || null,
      voltage_stages: normalizedVoltageStages,
      relay_ch1_stages: relayCh1Stages,
      relay_ch2_stages: relayCh2Stages,
      sampling_rate_hz: samplingRate,
      dmm_acquisition_mode: dmmAcquisitionMode,
      stop_after_seconds: stopAtEnabled ? stopAfterSeconds : null,
      record_camera: recordCamera,
      camera_ready_delay_seconds: recordCamera ? cameraReadyDelaySeconds : 0,
    };
  }, [
    cameraReadyDelaySeconds,
    dmm1Visa,
    dmm2Visa,
    dmmAcquisitionMode,
    measurementSource,
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
      setIsMeasuring(false);
      setSessionId(null);

      console.log("Measurement stopped:", data);
      alert(`Measurement saved to:\n${data.csv_path}`);
    } catch (error) {
      console.error("Error stopping measurement:", error);
      alert("Failed to stop measurement. Check console for details.");
    }
  };

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
  const cameraDownloadMessage = cameraDownloadStatus?.message || "No transfer started";
  const cameraDownloadDisabled =
    isMeasuring || isStarting || cameraStatus.recording || isCameraDownloadRunning;

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
          <div className="grid w-full gap-3 sm:w-auto sm:grid-cols-[auto_14rem_14rem] sm:items-center sm:justify-start xl:grid-cols-[auto_auto_auto_auto]">
            {/* Camera Status */}
            <div className="flex min-w-0 items-center gap-2 text-sm">
              {cameraStatus.recording ? (
                <Video className="h-5 w-5 text-red-500 animate-pulse" />
              ) : (
                <VideoOff className="h-5 w-5 text-muted-foreground" />
              )}
              <span className="truncate">
                {cameraStatus.recording ? "Recording" : "Camera Idle"}
              </span>
            </div>

            {/* Elapsed Time */}
            {isMeasuring && (
              <div className="text-sm font-mono sm:justify-self-end xl:justify-self-auto">
                {elapsedTime.toFixed(3)} s
              </div>
            )}

            {/* Start/Stop Buttons */}
            <Button
              onClick={handleStartMeasurement}
              disabled={isMeasuring || isStarting}
              size="lg"
              className="min-w-0 gap-2 px-4 sm:col-start-2 xl:col-start-auto"
            >
              {isStarting ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              ) : (
                <Play className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate">{isStarting ? "Starting" : "Start Measurement"}</span>
            </Button>
            <Button
              onClick={handleStopMeasurement}
              disabled={!isMeasuring || isStarting}
              variant="destructive"
              size="lg"
              className="min-w-0 gap-2 px-4"
            >
              <Square className="h-4 w-4 shrink-0" />
              <span className="truncate">Stop Measurement</span>
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
                <fieldset className="grid gap-3 sm:grid-cols-2">
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
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <VisaSelectControl
                      id="voltage-1-visa"
                      label="Voltage 1 VISA ID"
                      value={dmm1Visa}
                      resources={dmmResources}
                      onChange={setDmm1Visa}
                      disabled={isMeasuring || isStarting}
                    />
                    <VisaSelectControl
                      id="voltage-2-visa"
                      label="Voltage 2 VISA ID"
                      value={dmm2Visa}
                      resources={dmmResources}
                      onChange={setDmm2Visa}
                      disabled={isMeasuring || isStarting}
                    />
                  </div>
                )}
              </CardContent>
            </Card>
            <DMMGraph
              title="Voltage 1"
              data={dmm1Data}
            />
            <DMMGraph
              title="Voltage 2"
              data={dmm2Data}
            />
          </div>

          {/* Right Column - Controls */}
          <div className="min-w-0 space-y-6">
            <Card>
              <CardContent className="pt-6">
                <div className="grid gap-4 sm:grid-cols-2">
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
                  <div className="space-y-1.5">
                    <Label
                      htmlFor="sampling-rate"
                      className="text-xs uppercase tracking-wide text-muted-foreground"
                    >
                      Sampling Rate (Hz)
                    </Label>
                    <Input
                      id="sampling-rate"
                      type="number"
                      value={samplingRate}
                      onChange={(e) => setSamplingRate(parseFloat(e.target.value) || 10)}
                      disabled={isMeasuring || isStarting}
                      min={1}
                      max={300}
                      className="h-9"
                    />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
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
                      disabled={isMeasuring || isStarting || measurementSource === "oscilloscope"}
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
                </div>
                <div className="mt-4 grid gap-3 rounded-md border border-border px-3 py-2 sm:grid-cols-[1fr_auto_auto] sm:items-center">
                  <div className="min-w-0">
                    <Label className="text-sm font-medium">Config file</Label>
                    <div
                      className={
                        configLoadError
                          ? "truncate text-xs text-destructive"
                          : "truncate text-xs text-muted-foreground"
                      }
                    >
                      {configLoadError ||
                        (isSavingConfig ? "Saving..." : loadedConfigName) ||
                        "No config loaded"}
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
                <div className="mt-4 grid gap-3 rounded-md border border-border px-3 py-2 sm:grid-cols-[1fr_8rem] sm:items-center">
                  <div className="flex min-w-0 items-center gap-3">
                    <input
                      id="stop-at-enabled"
                      type="checkbox"
                      checked={stopAtEnabled}
                      onChange={(event) => setStopAtEnabled(event.target.checked)}
                      disabled={isMeasuring || isStarting}
                      className="h-4 w-4 rounded border-input"
                    />
                    <div className="min-w-0">
                      <Label htmlFor="stop-at-enabled" className="text-sm font-medium">
                        Auto stop
                      </Label>
                      <div className="text-xs text-muted-foreground">
                        {stopAtEnabled ? `${stopAfterSeconds} s` : "Manual stop"}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label
                      htmlFor="stop-after-seconds"
                      className="text-xs uppercase tracking-wide text-muted-foreground"
                    >
                      Stop At (s)
                    </Label>
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
                </div>
                <div className="mt-4 grid gap-3 rounded-md border border-border px-3 py-2 sm:grid-cols-[1fr_8rem] sm:items-center">
                  <div className="flex min-w-0 items-center gap-3">
                    <input
                      id="record-camera"
                      type="checkbox"
                      checked={recordCamera}
                      onChange={(event) => setRecordCamera(event.target.checked)}
                      disabled={isMeasuring || isStarting}
                      className="h-4 w-4 rounded border-input"
                    />
                    <div className="min-w-0">
                      <Label htmlFor="record-camera" className="text-sm font-medium">
                        Record camera
                      </Label>
                      <div className="text-xs text-muted-foreground">
                        {cameraStatus.available ? "Camera service ready" : "Camera unavailable"}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label
                      htmlFor="camera-ready-delay"
                      className="text-xs uppercase tracking-wide text-muted-foreground"
                    >
                      Ready Delay (s)
                    </Label>
                    <Input
                      id="camera-ready-delay"
                      type="number"
                      value={cameraReadyDelaySeconds}
                      onChange={(event) =>
                        setCameraReadyDelaySeconds(
                          Math.max(0, parseFloat(event.target.value) || 0)
                        )
                      }
                      disabled={!recordCamera || isMeasuring || isStarting}
                      min={0}
                      max={30}
                      step={0.1}
                      className="h-9"
                    />
                  </div>
                </div>
                <div className="mt-4 grid gap-3 rounded-md border border-border px-3 py-2 sm:grid-cols-[1fr_auto] sm:items-center">
                  <div className="min-w-0">
                    <Label className="text-sm font-medium">Latest recording</Label>
                    <div
                      className={
                        cameraDownloadStatus?.success === false
                          ? "truncate text-xs text-destructive"
                          : "truncate text-xs text-muted-foreground"
                      }
                    >
                      {cameraDownloadMessage}
                    </div>
                  </div>
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
                    {isCameraDownloadRunning ? "Downloading" : "Download Recording"}
                  </Button>
                </div>
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
