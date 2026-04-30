"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { DMMGraph } from "@/components/DMMGraph";
import { VoltageStageConfigurator, VoltageStage } from "@/components/VoltageStageConfigurator";
import { RelayStageConfigurator, RelayStage } from "@/components/RelayStageConfigurator";
import { Loader2, Play, Square, Video, VideoOff, AlertCircle } from "lucide-react";
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

interface VisaResourceOption {
  resource: string;
  label: string;
  idn?: string | null;
  kind?: string;
}

const MAX_DATA_POINTS = 500; // Limit data points shown on graph

export default function Home() {
  // State for measurements
  const [isMeasuring, setIsMeasuring] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [cameraStatus, setCameraStatus] = useState({ recording: false, available: false });
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const [latestTiming, setLatestTiming] = useState<{
    readDurationMs?: number | null;
    loopDurationMs?: number | null;
    overrun?: boolean;
  }>({});

  // State for instruments
  const [dmmResources, setDmmResources] = useState<VisaResourceOption[]>([]);
  const [powerSupplyResources, setPowerSupplyResources] = useState<VisaResourceOption[]>([]);
  const [serialPorts, setSerialPorts] = useState<string[]>([]);

  // State for DMM configuration
  const [dmm1Visa, setDmm1Visa] = useState("");
  const [dmm2Visa, setDmm2Visa] = useState("");
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
  const [recordCamera, setRecordCamera] = useState(false);
  const [cameraReadyDelaySeconds, setCameraReadyDelaySeconds] = useState(1);

  // WebSocket reference
  const wsRef = useRef<WebSocket | null>(null);
  const isMeasuringRef = useRef(false);

  // Fetch available instruments on mount
  useEffect(() => {
    fetchInstruments();
    fetchStatus();
  }, []);

  useEffect(() => {
    isMeasuringRef.current = isMeasuring;
  }, [isMeasuring]);

  const fetchInstruments = async () => {
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
      const nextPowerSupplyResources = toOptions(
        data.power_supply_resources || data.visa_resources || []
      );

      setDmmResources(nextDmmResources);
      setPowerSupplyResources(nextPowerSupplyResources);
      setSerialPorts(data.serial_ports || []);
      setDmm1Visa((value) =>
        value && !nextDmmResources.some((option) => option.resource === value) ? "" : value
      );
      setDmm2Visa((value) =>
        value && !nextDmmResources.some((option) => option.resource === value) ? "" : value
      );
      setPowerSupplyVisa((value) =>
        value && !nextPowerSupplyResources.some((option) => option.resource === value)
          ? ""
          : value
      );
    } catch (error) {
      console.error("Failed to fetch instruments:", error);
    }
  };

  const fetchStatus = async () => {
    try {
      const response = await fetch("/api/status");
      const data = await response.json();
      setIsMeasuring(data.is_measuring);
      setCameraStatus({
        recording: data.camera_recording,
        available: data.camera_available,
      });
      setSessionId(data.session_id);
      setElapsedTime(data.elapsed_time || 0);
    } catch (error) {
      console.error("Failed to fetch status:", error);
    }
  };

  // WebSocket connection
  const connectWebSocket = useCallback(() => {
    const ws = new WebSocket("ws://localhost:8000/api/live");

    ws.onopen = () => {
      console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
      try {
        const reading: DMMReading = JSON.parse(event.data);

        if (reading.time !== null && reading.time !== undefined) {
          // Update DMM1 data
          if (reading.dmm1_voltage !== null) {
            setDmm1Data((prev) => {
              const newData = [
                ...prev,
                { time: reading.time, voltage: reading.dmm1_voltage! },
              ];
              // Limit data points
              return newData.slice(-MAX_DATA_POINTS);
            });
          }

          // Update DMM2 data
          if (reading.dmm2_voltage !== null) {
            setDmm2Data((prev) => {
              const newData = [
                ...prev,
                { time: reading.time, voltage: reading.dmm2_voltage! },
              ];
              // Limit data points
              return newData.slice(-MAX_DATA_POINTS);
            });
          }

          setElapsedTime(reading.time);
          setLatestTiming({
            readDurationMs: reading.read_duration_ms,
            loopDurationMs: reading.loop_duration_ms,
            overrun: reading.overrun,
          });
        }
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
  }, []);

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

  const handleStartMeasurement = async () => {
    try {
      if (dmm1Visa && dmm2Visa && dmm1Visa === dmm2Visa) {
        alert("DMM1 and DMM2 must use different VISA IDs.");
        return;
      }

      if (voltageStages.length > 0 && !powerSupplyVisa) {
        alert("Add a power supply VISA ID before using power supply stages.");
        return;
      }

      if ((relayCh1Stages.length > 0 || relayCh2Stages.length > 0) && !relayPort) {
        alert("Add a relay board serial port before using relay stages.");
        return;
      }

      const normalizedVoltageStages: Array<{ start_time: number; end_time: number; voltage: number }> = [];

      for (let i = 0; i < voltageStages.length; i++) {
        const stage = voltageStages[i];
        if (stage.end_time <= stage.start_time) {
          alert(`Power stage ${i + 1}: end time must be after start time.`);
          return;
        }

        const expression = stage.voltageExpression ?? String(stage.voltage);
        const evaluation = evaluateExpression(expression, { t: stage.start_time });

        if (evaluation.error || evaluation.value === null) {
          alert(`Power stage ${i + 1}: ${evaluation.error ?? "Invalid expression"}`);
          return;
        }

        normalizedVoltageStages.push({
          start_time: stage.start_time,
          end_time: stage.end_time,
          voltage: evaluation.value,
        });
      }

      if (normalizedVoltageStages.length !== voltageStages.length) {
        alert("Failed to normalize voltage stages.");
        return;
      }

      for (const [channel, stages] of [
        [1, relayCh1Stages],
        [2, relayCh2Stages],
      ] as const) {
        for (let i = 0; i < stages.length; i++) {
          const stage = stages[i];
          if (stage.end_time <= stage.start_time) {
            alert(`Relay CH${channel} stage ${i + 1}: end time must be after start time.`);
            return;
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

      setIsStarting(true);

      const config = {
        test_name: testName,
        dmm1_visa_id: dmm1Visa || null,
        dmm2_visa_id: dmm2Visa || null,
        power_supply_visa_id: powerSupplyVisa || null,
        relay_port: relayPort || null,
        voltage_stages: normalizedVoltageStages,
        relay_ch1_stages: relayCh1Stages,
        relay_ch2_stages: relayCh2Stages,
        sampling_rate_hz: samplingRate,
        record_camera: recordCamera,
        camera_ready_delay_seconds: recordCamera ? cameraReadyDelaySeconds : 0,
      };

      const response = await fetch("/api/start_measurement", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });

      if (!response.ok) {
        const error = await response.json();
        alert(`Failed to start measurement: ${error.detail}`);
        setIsStarting(false);
        return;
      }

      const data = await response.json();
      setSessionId(data.session_id);
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

  const handleStopMeasurement = async () => {
    try {
      const response = await fetch("/api/stop_measurement", {
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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column - DMM Graphs */}
          <div className="min-w-0 lg:col-span-2 space-y-6">
            <DMMGraph
              title="DMM 1"
              data={dmm1Data}
              visaResources={dmmResources}
              selectedVisa={dmm1Visa}
              onVisaChange={setDmm1Visa}
            />
            <DMMGraph
              title="DMM 2"
              data={dmm2Data}
              visaResources={dmmResources}
              selectedVisa={dmm2Visa}
              onVisaChange={setDmm2Visa}
            />
          </div>

          {/* Right Column - Controls */}
          <div className="space-y-6">
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
