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
import { cn } from "@/lib/utils";
import { evaluateExpression } from "@/lib/expression";

export interface VoltageStage {
  start_time: number;
  end_time: number;
  voltage: number;
  voltageExpression?: string;
  voltageExpressionError?: string;
}

interface VoltageStageConfiguratorProps {
  stages: VoltageStage[];
  onStagesChange: (stages: VoltageStage[]) => void;
  visaResources: Array<{ resource: string; label: string }>;
  selectedVisa: string;
  onVisaChange: (visa: string) => void;
  disabled?: boolean;
}

export function VoltageStageConfigurator({
  stages,
  onStagesChange,
  visaResources,
  selectedVisa,
  onVisaChange,
  disabled = false,
}: VoltageStageConfiguratorProps) {
  const addStage = () => {
    if (stages.length >= 10) return;

    const lastStage = stages[stages.length - 1];
    const baseExpression =
      lastStage?.voltageExpression ?? (lastStage ? String(lastStage.voltage) : "0");
    const startTime = lastStage ? lastStage.end_time : 0;
    const endTime = lastStage ? lastStage.end_time + 5 : 5;
    const evaluation = evaluateExpression(baseExpression, { t: startTime });

    const newStage: VoltageStage = {
      start_time: startTime,
      end_time: endTime,
      voltage:
        evaluation.error || evaluation.value === null
          ? lastStage?.voltage ?? 0
          : evaluation.value,
      voltageExpression: baseExpression,
      voltageExpressionError: evaluation.error,
    };

    onStagesChange([...stages, newStage]);
  };

  const removeStage = (index: number) => {
    const filtered = stages.filter((_, i) => i !== index);
    onStagesChange(filtered);
  };

  const updateStage = (index: number, field: keyof VoltageStage, value: number) => {
    const newStages = stages.map((stage, i) => {
      if (i !== index) return stage;

      const updatedStage: VoltageStage = { ...stage, [field]: value };

      if (field === "start_time" && updatedStage.voltageExpression) {
        const evaluation = evaluateExpression(updatedStage.voltageExpression, {
          t: value,
        });

        updatedStage.voltage =
          evaluation.error || evaluation.value === null ? stage.voltage : evaluation.value;
        updatedStage.voltageExpressionError = evaluation.error;
      }

      return updatedStage;
    });

    onStagesChange(newStages);
  };

  const handleVoltageExpressionChange = (index: number, expression: string) => {
    const newStages = stages.map((stage, i) => {
      if (i !== index) return stage;

      const evaluation = evaluateExpression(expression, { t: stage.start_time });

      return {
        ...stage,
        voltage:
          evaluation.error || evaluation.value === null ? stage.voltage : evaluation.value,
        voltageExpression: expression,
        voltageExpressionError: evaluation.error,
      };
    });

    onStagesChange(newStages);
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle>Power Supply</CardTitle>
        </div>
        <div className="flex w-full flex-col gap-1 sm:max-w-xs">
          <Label htmlFor="power-supply-visa" className="text-xs text-muted-foreground">
            VISA ID
          </Label>
          <Select
            value={selectedVisa}
            onValueChange={onVisaChange}
            disabled={disabled}
          >
            <SelectTrigger id="power-supply-visa" className="h-9">
              <SelectValue placeholder="Select power supply" />
            </SelectTrigger>
            <SelectContent>
              {visaResources.length === 0 ? (
                <SelectItem value="none" disabled>
                  No power supplies found
                </SelectItem>
              ) : (
                visaResources.map((visa) => (
                  <SelectItem key={visa.resource} value={visa.resource}>
                    {visa.label}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
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
                  htmlFor={`stage-${index}-start`}
                  className="text-xs text-muted-foreground"
                >
                  Start (s)
                </Label>
                <Input
                  id={`stage-${index}-start`}
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
                  htmlFor={`stage-${index}-end`}
                  className="text-xs text-muted-foreground"
                >
                  End (s)
                </Label>
                <Input
                  id={`stage-${index}-end`}
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
                  htmlFor={`stage-${index}-voltage`}
                  className="text-xs text-muted-foreground"
                >
                  Voltage (V)
                </Label>
                <Input
                  id={`stage-${index}-voltage`}
                  value={stage.voltageExpression ?? String(stage.voltage)}
                  onChange={(e) => handleVoltageExpressionChange(index, e.target.value)}
                  disabled={disabled}
                  placeholder="e.g. 5t+1"
                  aria-invalid={Boolean(stage.voltageExpressionError)}
                  className={cn(
                    "h-9",
                    stage.voltageExpressionError &&
                      "border-destructive focus-visible:ring-destructive/40"
                  )}
                />
                {stage.voltageExpressionError && (
                  <span className="text-xs text-destructive">
                    {stage.voltageExpressionError}
                  </span>
                )}
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
