"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
  disabled?: boolean;
}

export function RelayStageConfigurator({
  channel,
  stages,
  onStagesChange,
  disabled = false,
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
    onStagesChange(stages.filter((_, i) => i !== index));
  };

  const updateStage = (
    index: number,
    field: keyof RelayStage,
    value: number | "open" | "closed"
  ) => {
    const newStages = [...stages];
    newStages[index] = { ...newStages[index], [field]: value };
    onStagesChange(newStages);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Relay Channel {channel}</CardTitle>
        <CardDescription>Configure up to 10 stages (max 10)</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {stages.map((stage, index) => (
          <div key={index} className="flex items-end gap-2">
            <div className="flex-1 space-y-2">
              <Label htmlFor={`relay${channel}-${index}-start`} className="text-xs">
                Stage {index + 1}
              </Label>
              <div className="grid grid-cols-3 gap-2">
                <div>
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
                <div>
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
                <div>
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
                    <SelectTrigger id={`relay${channel}-${index}-state`} className="h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="open">Open</SelectItem>
                      <SelectItem value="closed">Closed</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
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

