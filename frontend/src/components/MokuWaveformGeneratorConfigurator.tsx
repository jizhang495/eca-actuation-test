"use client";

import React from "react";
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

export type MokuWaveform = "Sine" | "Square" | "Ramp" | "Pulse";

export interface MokuWaveformGeneratorStage {
  start_time: number;
  end_time: number;
  waveform: MokuWaveform;
  vpp: number;
  frequency_hz: number;
}

interface MokuWaveformGeneratorConfiguratorProps {
  stages: MokuWaveformGeneratorStage[];
  onStagesChange: (stages: MokuWaveformGeneratorStage[]) => void;
  disabled?: boolean;
}

const WAVEFORMS: MokuWaveform[] = ["Sine", "Square", "Ramp", "Pulse"];
const MAX_MOKU_WAVEFORM_VPP = 2.0;

export function MokuWaveformGeneratorConfigurator({
  stages,
  onStagesChange,
  disabled = false,
}: MokuWaveformGeneratorConfiguratorProps) {
  const addStage = () => {
    if (stages.length >= 30) return;

    const lastStage = stages[stages.length - 1];
    const startTime = lastStage ? lastStage.end_time : 0;
    const newStage: MokuWaveformGeneratorStage = {
      start_time: startTime,
      end_time: startTime + 5,
      waveform: lastStage?.waveform ?? "Sine",
      vpp: lastStage?.vpp ?? 0,
      frequency_hz: lastStage?.frequency_hz ?? 1,
    };

    onStagesChange([...stages, newStage]);
  };

  const removeStage = (index: number) => {
    onStagesChange(stages.filter((_, i) => i !== index));
  };

  const updateStage = <K extends keyof MokuWaveformGeneratorStage>(
    index: number,
    field: K,
    value: MokuWaveformGeneratorStage[K]
  ) => {
    onStagesChange(
      stages.map((stage, i) => (i === index ? { ...stage, [field]: value } : stage))
    );
  };

  return (
    <div className="space-y-3">
        {stages.map((stage, index) => (
          <div key={index} className="flex items-end gap-3">
            <span className="w-5 text-right text-xs font-medium text-muted-foreground">
              {index + 1}
            </span>
            <div className="grid flex-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <div className="flex flex-col gap-1">
                <Label htmlFor={`moku-sg-${index}-start`} className="text-xs text-muted-foreground">
                  Start (s)
                </Label>
                <Input
                  id={`moku-sg-${index}-start`}
                  type="number"
                  step="0.1"
                  value={stage.start_time}
                  onChange={(event) =>
                    updateStage(index, "start_time", parseFloat(event.target.value) || 0)
                  }
                  disabled={disabled}
                  className="h-9"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor={`moku-sg-${index}-end`} className="text-xs text-muted-foreground">
                  End (s)
                </Label>
                <Input
                  id={`moku-sg-${index}-end`}
                  type="number"
                  step="0.1"
                  value={stage.end_time}
                  onChange={(event) =>
                    updateStage(index, "end_time", parseFloat(event.target.value) || 0)
                  }
                  disabled={disabled}
                  className="h-9"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label
                  htmlFor={`moku-sg-${index}-waveform`}
                  className="text-xs text-muted-foreground"
                >
                  Waveform
                </Label>
                <Select
                  value={stage.waveform}
                  onValueChange={(value) =>
                    updateStage(index, "waveform", value as MokuWaveform)
                  }
                  disabled={disabled}
                >
                  <SelectTrigger id={`moku-sg-${index}-waveform`} className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {WAVEFORMS.map((waveform) => (
                      <SelectItem key={waveform} value={waveform}>
                        {waveform}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor={`moku-sg-${index}-vpp`} className="text-xs text-muted-foreground">
                  Vpp
                </Label>
                <Input
                  id={`moku-sg-${index}-vpp`}
                  type="number"
                  min={0}
                  max={MAX_MOKU_WAVEFORM_VPP}
                  step="0.001"
                  value={stage.vpp}
                  onChange={(event) =>
                    updateStage(
                      index,
                      "vpp",
                      Math.min(
                        MAX_MOKU_WAVEFORM_VPP,
                        Math.max(0, parseFloat(event.target.value) || 0)
                      )
                    )
                  }
                  disabled={disabled}
                  className="h-9"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label
                  htmlFor={`moku-sg-${index}-frequency`}
                  className="text-xs text-muted-foreground"
                >
                  Frequency (Hz)
                </Label>
                <Input
                  id={`moku-sg-${index}-frequency`}
                  type="number"
                  min={0.000001}
                  step="1"
                  value={stage.frequency_hz}
                  onChange={(event) =>
                    updateStage(
                      index,
                      "frequency_hz",
                      Math.max(0.000001, parseFloat(event.target.value) || 0.000001)
                    )
                  }
                  disabled={disabled}
                  className="h-9"
                />
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
          disabled={disabled || stages.length >= 30}
          variant="outline"
          className="w-full"
        >
          <Plus className="mr-2 h-4 w-4" />
          Add Stage {stages.length > 0 && `(${stages.length}/30)`}
        </Button>
    </div>
  );
}
