"use client";

import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Trash2 } from "lucide-react";

export interface RelayStage {
  start_time: number;
  end_time: number;
  state: "open" | "closed";
}

interface RelayStageConfiguratorProps {
  channel: number;
  stages: RelayStage[];
  onStagesChange: (stages: RelayStage[]) => void;
  serialPorts: string[];
  selectedPort: string;
  onPortChange: (port: string) => void;
  disabled?: boolean;
  showPortSelector?: boolean;
}

export function RelayStageConfigurator({
  channel,
  stages,
  onStagesChange,
  serialPorts,
  selectedPort,
  onPortChange,
  disabled = false,
  showPortSelector = true,
}: RelayStageConfiguratorProps) {
  const addStage = () => {
    if (stages.length >= 10) return;

    const lastStage = stages[stages.length - 1];
    const newStage: RelayStage = {
      start_time: lastStage ? lastStage.end_time : 0,
      end_time: lastStage ? lastStage.end_time + 5 : 5,
      state: "open",
    };

    onStagesChange([...stages, newStage]);
  };

  const removeStage = (index: number) => {
    const filtered = stages.filter((_, i) => i !== index);
    onStagesChange(filtered);
  };

  const updateStage = (
    index: number,
    field: keyof RelayStage,
    value: number | "open" | "closed"
  ) => {
    const newStages = stages.map((stage, i) =>
      i === index ? { ...stage, [field]: value } : stage
    );

    onStagesChange(newStages);
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle>Relay CH{channel}</CardTitle>
        </div>
        {showPortSelector && (
          <div className="flex w-full flex-col gap-1 sm:max-w-xs">
            <Label htmlFor={`relay${channel}-port`} className="text-xs text-muted-foreground">
              Serial Port
            </Label>
            <Select
              value={selectedPort}
              onValueChange={onPortChange}
              disabled={disabled}
            >
              <SelectTrigger id={`relay${channel}-port`} className="h-9">
                <SelectValue placeholder="Select relay port" />
              </SelectTrigger>
              <SelectContent>
                {serialPorts.length === 0 ? (
                  <SelectItem value="none" disabled>
                    No relay ports found
                  </SelectItem>
                ) : (
                  serialPorts.map((port) => (
                    <SelectItem key={port} value={port}>
                      {port}
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {stages.map((stage, index) => (
          <div key={index} className="flex items-end gap-3">
            <span className="w-5 text-xs font-medium text-muted-foreground text-right">
              {index + 1}
            </span>
            <div className="grid flex-1 grid-cols-3 gap-2">
              <div className="flex flex-col gap-1">
                <Label
                  htmlFor={`relay${channel}-${index}-start`}
                  className="text-xs text-muted-foreground"
                >
                  Start (s)
                </Label>
                <Input
                  id={`relay${channel}-${index}-start`}
                  type="number"
                  step="0.1"
                  value={stage.start_time}
                  onChange={(e) =>
                    updateStage(index, "start_time", parseFloat(e.target.value) || 0)
                  }
                  disabled={disabled}
                  className="h-9"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label
                  htmlFor={`relay${channel}-${index}-end`}
                  className="text-xs text-muted-foreground"
                >
                  End (s)
                </Label>
                <Input
                  id={`relay${channel}-${index}-end`}
                  type="number"
                  step="0.1"
                  value={stage.end_time}
                  onChange={(e) =>
                    updateStage(index, "end_time", parseFloat(e.target.value) || 0)
                  }
                  disabled={disabled}
                  className="h-9"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label
                  htmlFor={`relay${channel}-${index}-state`}
                  className="text-xs text-muted-foreground"
                >
                  State
                </Label>
                <Select
                  value={stage.state}
                  onValueChange={(value) =>
                    updateStage(index, "state", value as "open" | "closed")
                  }
                  disabled={disabled}
                >
                  <SelectTrigger
                    id={`relay${channel}-${index}-state`}
                    className="h-9"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="open">Open</SelectItem>
                    <SelectItem value="closed">Closed</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <Button
              variant="destructive"
              size="icon"
              onClick={() => removeStage(index)}
              disabled={disabled}
              className="h-9 w-9"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}

        <Button
          onClick={addStage}
          disabled={disabled || stages.length >= 10}
          variant="outline"
          className="w-full"
        >
          <Plus className="h-4 w-4 mr-2" />
          Add Stage {stages.length > 0 && `(${stages.length}/10)`}
        </Button>
      </CardContent>
    </Card>
  );
}
