"use client";

import React, { useState, useCallback, useMemo } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

interface DMMGraphProps {
  title: string;
  data: Array<{ time: number; voltage: number }>;
  visaResources: string[];
  selectedVisa: string;
  onVisaChange: (value: string) => void;
}

export function DMMGraph({
  title,
  data,
  visaResources,
  selectedVisa,
  onVisaChange,
}: DMMGraphProps) {
  const [xDomain, setXDomain] = useState<[number, number] | undefined>(undefined);
  const [yDomain, setYDomain] = useState<[number, number] | undefined>(undefined);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number; xDomain: [number, number]; yDomain: [number, number] } | null>(null);
  const [userHasInteracted, setUserHasInteracted] = useState(false);

  // Calculate domains for autoscaling
  const dataRange = useMemo(() => {
    if (data.length === 0) return null;
    
    const times = data.map((d) => d.time);
    const voltages = data.map((d) => d.voltage).filter((v) => v !== null && !isNaN(v)) as number[];
    
    if (times.length === 0 || voltages.length === 0) return null;
    
    const timeMin = Math.min(...times);
    const timeMax = Math.max(...times);
    const voltageMin = Math.min(...voltages);
    const voltageMax = Math.max(...voltages);
    
    const timeRange = timeMax - timeMin;
    const voltageRange = voltageMax - voltageMin;
    
    // Add small padding (2% on each side)
    const timePadding = timeRange * 0.02 || 0.1;
    const voltagePadding = voltageRange * 0.02 || 0.1;
    
    return {
      time: [timeMin - timePadding, timeMax + timePadding] as [number, number],
      voltage: [voltageMin - voltagePadding, voltageMax + voltagePadding] as [number, number],
    };
  }, [data]);

  // Auto-scale when data updates (if user hasn't manually interacted)
  React.useEffect(() => {
    if (dataRange && !userHasInteracted) {
      setXDomain(dataRange.time);
      setYDomain(dataRange.voltage);
    }
  }, [dataRange, userHasInteracted]);

  // Handle mouse drag for panning
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!xDomain || !yDomain) return;
    
    if (e.button === 0) { // Left mouse button
      setUserHasInteracted(true);
      setIsDragging(true);
      setDragStart({
        x: e.clientX,
        y: e.clientY,
        xDomain,
        yDomain,
      });
    }
  }, [xDomain, yDomain]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!isDragging || !dragStart || !dataRange) return;
    
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    
    const rect = e.currentTarget.getBoundingClientRect();
    const xScale = (dragStart.xDomain[1] - dragStart.xDomain[0]) / rect.width;
    const yScale = (dragStart.yDomain[1] - dragStart.yDomain[0]) / rect.height;
    
    const newXDomain: [number, number] = [
      dragStart.xDomain[0] - dx * xScale,
      dragStart.xDomain[1] - dx * xScale,
    ];
    
    const newYDomain: [number, number] = [
      dragStart.yDomain[0] + dy * yScale,
      dragStart.yDomain[1] + dy * yScale,
    ];
    
    // Clamp to data bounds
    const clampedXDomain: [number, number] = [
      Math.max(dataRange.time[0], newXDomain[0]),
      Math.min(dataRange.time[1], newXDomain[1]),
    ];
    
    const clampedYDomain: [number, number] = [
      Math.max(dataRange.voltage[0], newYDomain[0]),
      Math.min(dataRange.voltage[1], newYDomain[1]),
    ];
    
    setXDomain(clampedXDomain);
    setYDomain(clampedYDomain);
  }, [isDragging, dragStart, dataRange]);

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
    setDragStart(null);
  }, []);

  // Reset zoom on double click
  const handleDoubleClick = useCallback(() => {
    if (dataRange) {
      setUserHasInteracted(false);
      setXDomain(dataRange.time);
      setYDomain(dataRange.voltage);
    }
  }, [dataRange]);

  return (
    <Card className="w-full">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4">
          <CardTitle className="text-lg">{title}</CardTitle>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Label htmlFor={`visa-${title}`} className="text-sm whitespace-nowrap">
              VISA ID:
            </Label>
            <Select value={selectedVisa} onValueChange={onVisaChange}>
              <SelectTrigger id={`visa-${title}`} className="w-[180px]">
                <SelectValue placeholder="Select instrument" />
              </SelectTrigger>
              <SelectContent>
                {visaResources.length === 0 ? (
                  <SelectItem value="none" disabled>
                    No instruments found
                  </SelectItem>
                ) : (
                  visaResources.map((visa) => (
                    <SelectItem key={visa} value={visa}>
                      {visa}
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0 pb-4 px-4">
        <div
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onDoubleClick={handleDoubleClick}
          style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
          className="select-none"
        >
          <ResponsiveContainer width="100%" height={280}>
            <LineChart
              data={data}
              margin={{ top: 5, right: 10, bottom: 20, left: 10 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis
                dataKey="time"
                domain={xDomain}
                type="number"
                scale="linear"
                label={{ value: "Time (s)", position: "insideBottom", offset: -5 }}
                tick={{ fontSize: 12 }}
              />
              <YAxis
                domain={yDomain}
                type="number"
                scale="linear"
                label={{ value: "Voltage (V)", angle: -90, position: "insideLeft" }}
                tick={{ fontSize: 12 }}
              />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="voltage"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                connectNulls={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

