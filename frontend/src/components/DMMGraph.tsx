"use client";

import React from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Live voltage measurement</CardDescription>
        <div className="flex items-center gap-2 mt-4">
          <Label htmlFor={`visa-${title}`} className="min-w-fit">
            VISA ID:
          </Label>
          <Select value={selectedVisa} onValueChange={onVisaChange}>
            <SelectTrigger id={`visa-${title}`} className="w-full">
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
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="time"
              label={{ value: "Time (s)", position: "insideBottom", offset: -5 }}
            />
            <YAxis
              label={{ value: "Voltage (V)", angle: -90, position: "insideLeft" }}
            />
            <Tooltip />
            <Legend />
            <Line
              type="monotone"
              dataKey="voltage"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

