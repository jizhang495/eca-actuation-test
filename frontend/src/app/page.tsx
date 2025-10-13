"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { DMMGraph } from "@/components/DMMGraph";
import { VoltageStageConfigurator, VoltageStage } from "@/components/VoltageStageConfigurator";
import { RelayStageConfigurator, RelayStage } from "@/components/RelayStageConfigurator";
import { Play, Square, Video, VideoOff, AlertCircle } from "lucide-react";

interface DMMReading {
  time: number;
  dmm1_voltage: number | null;
  dmm2_voltage: number | null;
}

const MAX_DATA_POINTS = 500; // Limit data points shown on graph

export default function Home() {
  // State for measurements
  const [isMeasuring, setIsMeasuring] = useState(false);
  const [cameraStatus, setCameraStatus] = useState({ recording: false, available: false });
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [elapsedTime, setElapsedTime] = useState<number>(0);

  // State for instruments
  const [visaResources, setVisaResources] = useState<string[]>([]);
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

  // WebSocket reference
  const wsRef = useRef<WebSocket | null>(null);

  // Fetch available instruments on mount
  useEffect(() => {
    fetchInstruments();
    fetchStatus();
  }, []);

  const fetchInstruments = async () => {
    try {
      const response = await fetch("/api/list_instruments");
      const data = await response.json();
      setVisaResources(data.visa_resources || []);
      setSerialPorts(data.serial_ports || []);
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
      if (isMeasuring) {
        setTimeout(connectWebSocket, 1000);
      }
    };

    wsRef.current = ws;
  }, [isMeasuring]);

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
      const config = {
        test_name: testName,
        dmm1_visa_id: dmm1Visa || null,
        dmm2_visa_id: dmm2Visa || null,
        power_supply_visa_id: powerSupplyVisa || null,
        relay_port: relayPort || null,
        voltage_stages: voltageStages,
        relay_ch1_stages: relayCh1Stages,
        relay_ch2_stages: relayCh2Stages,
        sampling_rate_hz: samplingRate,
      };

      const response = await fetch("/api/start_measurement", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });

      if (!response.ok) {
        const error = await response.json();
        alert(`Failed to start measurement: ${error.detail}`);
        return;
      }

      const data = await response.json();
      setSessionId(data.session_id);
      setIsMeasuring(true);

      // Clear previous data
      setDmm1Data([]);
      setDmm2Data([]);
      setElapsedTime(0);

      console.log("Measurement started:", data);
    } catch (error) {
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
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold">ECA Testing Webapp</h1>
              <p className="text-sm text-muted-foreground">
                Electrochemical Actuator Testing and Control
              </p>
            </div>
            <div className="flex items-center gap-4">
              {/* Camera Status */}
              <div className="flex items-center gap-2">
                {cameraStatus.recording ? (
                  <Video className="h-5 w-5 text-red-500 animate-pulse" />
                ) : (
                  <VideoOff className="h-5 w-5 text-muted-foreground" />
                )}
                <span className="text-sm">
                  {cameraStatus.recording ? "Recording" : "Camera Idle"}
                </span>
              </div>

              {/* Elapsed Time */}
              {isMeasuring && (
                <div className="text-sm font-mono">
                  {elapsedTime.toFixed(1)} s
                </div>
              )}

              {/* Start/Stop Buttons */}
              <Button
                onClick={handleStartMeasurement}
                disabled={isMeasuring}
                size="lg"
                className="gap-2"
              >
                <Play className="h-4 w-4" />
                Start Measurement
              </Button>
              <Button
                onClick={handleStopMeasurement}
                disabled={!isMeasuring}
                variant="destructive"
                size="lg"
                className="gap-2"
              >
                <Square className="h-4 w-4" />
                Stop Measurement
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column - DMM Graphs */}
          <div className="lg:col-span-2 space-y-6">
            <DMMGraph
              title="DMM 1"
              data={dmm1Data}
              visaResources={visaResources}
              selectedVisa={dmm1Visa}
              onVisaChange={setDmm1Visa}
            />
            <DMMGraph
              title="DMM 2"
              data={dmm2Data}
              visaResources={visaResources}
              selectedVisa={dmm2Visa}
              onVisaChange={setDmm2Visa}
            />
          </div>

          {/* Right Column - Controls */}
          <div className="space-y-6">
            {/* Test Configuration */}
            <Card>
              <CardHeader>
                <CardTitle>Test Configuration</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label htmlFor="test-name">Test Name</Label>
                  <Input
                    id="test-name"
                    value={testName}
                    onChange={(e) => setTestName(e.target.value)}
                    disabled={isMeasuring}
                    placeholder="test"
                  />
                </div>
                <div>
                  <Label htmlFor="sampling-rate">Sampling Rate (Hz)</Label>
                  <Input
                    id="sampling-rate"
                    type="number"
                    value={samplingRate}
                    onChange={(e) => setSamplingRate(parseFloat(e.target.value) || 10)}
                    disabled={isMeasuring}
                    min={1}
                    max={100}
                  />
                </div>
              </CardContent>
            </Card>

            {/* Power Supply Configuration */}
            <Card>
              <CardHeader>
                <CardTitle>Power Supply</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <Label htmlFor="power-supply-visa">VISA ID</Label>
                  <Select
                    value={powerSupplyVisa}
                    onValueChange={setPowerSupplyVisa}
                    disabled={isMeasuring}
                  >
                    <SelectTrigger id="power-supply-visa">
                      <SelectValue placeholder="Select power supply" />
                    </SelectTrigger>
                    <SelectContent>
                      {visaResources.map((visa) => (
                        <SelectItem key={visa} value={visa}>
                          {visa}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </CardContent>
            </Card>

            {/* Relay Configuration */}
            <Card>
              <CardHeader>
                <CardTitle>Relay Board</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <Label htmlFor="relay-port">Serial Port</Label>
                  <Select
                    value={relayPort}
                    onValueChange={setRelayPort}
                    disabled={isMeasuring}
                  >
                    <SelectTrigger id="relay-port">
                      <SelectValue placeholder="Select relay port" />
                    </SelectTrigger>
                    <SelectContent>
                      {serialPorts.map((port) => (
                        <SelectItem key={port} value={port}>
                          {port}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </CardContent>
            </Card>

            {/* Session Info */}
            {sessionId && (
              <Card>
                <CardHeader>
                  <CardTitle>Current Session</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm font-mono break-all">{sessionId}</p>
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        {/* Voltage and Relay Stage Configurators */}
        <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
          <VoltageStageConfigurator
            stages={voltageStages}
            onStagesChange={setVoltageStages}
            disabled={isMeasuring}
          />
          <RelayStageConfigurator
            channel={1}
            stages={relayCh1Stages}
            onStagesChange={setRelayCh1Stages}
            disabled={isMeasuring}
          />
          <RelayStageConfigurator
            channel={2}
            stages={relayCh2Stages}
            onStagesChange={setRelayCh2Stages}
            disabled={isMeasuring}
          />
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

