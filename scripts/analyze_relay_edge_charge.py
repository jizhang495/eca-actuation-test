#!/usr/bin/env python3
"""Analyze relay-edge current peaks and charge from waveform CSV data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
except ImportError as exc:
    print(
        "error: missing analysis dependency. Run with the project virtualenv "
        "or install matplotlib, numpy, and pandas.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


DEFAULT_SHUNT_OHMS = 330.0
WAVEFORM_CSV_NAMES = ("moku_waveform.csv", "oscilloscope_waveform.csv")
WAVEFORM_COLUMNS = ("time", "ch1_voltage", "ch2_voltage")


@dataclass(frozen=True)
class EdgeEvent:
    index: int
    relay_channel: str
    transition: str
    edge_time_s: float


@dataclass(frozen=True)
class TailFit:
    relative_s: np.ndarray
    current_ma: np.ndarray
    charge_uc: np.ndarray
    amplitude_mA: float
    tau_s: float
    charge_uC: float
    r2_log: float
    sample_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create relay-edge charge plots. Raw baseline-corrected current is "
            "integrated for charge; smoothing is only used as a visual overlay."
        )
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Session directory, moku_waveform.csv, or oscilloscope_waveform.csv",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Config JSON path. Defaults to config.json beside the waveform CSV.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to the waveform CSV folder.",
    )
    parser.add_argument(
        "--edge-time",
        type=float,
        action="append",
        help=(
            "Manual relay edge time in seconds. Can be passed multiple times. "
            "If omitted, relay_ch*_stages are read from config.json."
        ),
    )
    parser.add_argument(
        "--shunt-ohms",
        type=float,
        default=DEFAULT_SHUNT_OHMS,
        help="Current shunt resistance. Defaults to 330 ohm.",
    )
    parser.add_argument(
        "--amplifier-gain",
        type=float,
        default=1.0,
        help="External current-sense amplifier gain. Defaults to 1.",
    )
    parser.add_argument(
        "--window-before",
        type=float,
        default=0.2,
        help="Seconds to show before each relay edge. Defaults to 0.2.",
    )
    parser.add_argument(
        "--window-after",
        type=float,
        default=1.0,
        help="Seconds to show after each relay edge. Defaults to 1.0.",
    )
    parser.add_argument(
        "--baseline-start",
        type=float,
        default=-0.2,
        help="Baseline window start relative to the edge. Defaults to -0.2.",
    )
    parser.add_argument(
        "--baseline-end",
        type=float,
        default=-0.02,
        help="Baseline window end relative to the edge. Defaults to -0.02.",
    )
    parser.add_argument(
        "--integrate-start",
        type=float,
        default=0.0,
        help=(
            "Charge integration window start relative to the edge. Defaults to 0."
        ),
    )
    parser.add_argument(
        "--integrate-end",
        type=float,
        default=1.0,
        help="Charge integration window end relative to the edge. Defaults to 1.0.",
    )
    parser.add_argument(
        "--smooth-ms",
        type=float,
        default=5.0,
        help="Centered rolling-median smoothing width for display only. Defaults to 5 ms.",
    )
    return parser.parse_args()


def resolve_waveform_path(input_path: Path) -> Path:
    if input_path.is_file():
        return input_path
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    for csv_name in WAVEFORM_CSV_NAMES:
        candidate = input_path / csv_name
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"No waveform CSV found in {input_path}; expected one of {WAVEFORM_CSV_NAMES}"
    )


def load_waveform(csv_path: Path) -> pd.DataFrame:
    data = pd.read_csv(csv_path, usecols=list(WAVEFORM_COLUMNS))
    for column in WAVEFORM_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=list(WAVEFORM_COLUMNS)).sort_values("time")
    if data.empty:
        raise RuntimeError(f"Waveform CSV contains no plottable rows: {csv_path}")
    return data


def load_events(config_path: Path | None, manual_edge_times: list[float] | None) -> list[EdgeEvent]:
    if manual_edge_times:
        return [
            EdgeEvent(index=index + 1, relay_channel="manual", transition="edge", edge_time_s=time_s)
            for index, time_s in enumerate(manual_edge_times)
        ]

    if not config_path or not config_path.exists():
        raise FileNotFoundError(
            "No --edge-time values were provided and config.json was not found"
        )

    config = json.loads(config_path.read_text())
    events: list[EdgeEvent] = []
    for key in sorted(config):
        if not key.startswith("relay_ch") or not key.endswith("_stages"):
            continue

        relay_channel = key.removeprefix("relay_").removesuffix("_stages").upper()
        for stage in config.get(key, []):
            state = str(stage.get("state", "")).lower()
            start_time = stage.get("start_time")
            end_time = stage.get("end_time")
            if state == "closed" and start_time is not None:
                events.append(
                    EdgeEvent(0, relay_channel, "close", float(start_time))
                )
            if state == "closed" and end_time is not None:
                events.append(
                    EdgeEvent(0, relay_channel, "open", float(end_time))
                )

    events = sorted(events, key=lambda event: event.edge_time_s)
    if not events:
        raise RuntimeError(f"No relay edge events found in {config_path}")
    return [
        EdgeEvent(index=index + 1, relay_channel=event.relay_channel, transition=event.transition, edge_time_s=event.edge_time_s)
        for index, event in enumerate(events)
    ]


def validate_args(args: argparse.Namespace) -> None:
    if args.shunt_ohms <= 0:
        raise RuntimeError("--shunt-ohms must be greater than 0")
    if args.amplifier_gain <= 0:
        raise RuntimeError("--amplifier-gain must be greater than 0")
    if args.window_before <= 0 or args.window_after <= 0:
        raise RuntimeError("--window-before and --window-after must be greater than 0")
    if args.baseline_start >= args.baseline_end:
        raise RuntimeError("--baseline-start must be less than --baseline-end")
    if args.integrate_start >= args.integrate_end:
        raise RuntimeError("--integrate-start must be less than --integrate-end")
    if args.smooth_ms < 0:
        raise RuntimeError("--smooth-ms must be non-negative")


def current_ma(data: pd.DataFrame, shunt_ohms: float, amplifier_gain: float) -> np.ndarray:
    return data["ch2_voltage"].to_numpy(dtype=float) / (shunt_ohms * amplifier_gain) * 1000.0


def centered_rolling_median(values: np.ndarray, sample_interval_s: float, smooth_ms: float) -> np.ndarray:
    if smooth_ms <= 0 or not math.isfinite(sample_interval_s) or sample_interval_s <= 0:
        return values

    window_samples = max(1, int(round((smooth_ms / 1000.0) / sample_interval_s)))
    if window_samples % 2 == 0:
        window_samples += 1
    if window_samples <= 1:
        return values
    return (
        pd.Series(values)
        .rolling(window=window_samples, center=True, min_periods=1)
        .median()
        .to_numpy(dtype=float)
    )


def cumulative_trapezoid_uc(time_s: np.ndarray, current_ma_values: np.ndarray) -> np.ndarray:
    if len(time_s) == 0:
        return np.array([], dtype=float)
    if len(time_s) == 1:
        return np.array([0.0], dtype=float)

    dt = np.diff(time_s)
    average_current_ma = 0.5 * (current_ma_values[:-1] + current_ma_values[1:])
    increments_uc = average_current_ma * dt * 1000.0
    return np.concatenate(([0.0], np.cumsum(increments_uc)))


def integrate_uc(time_s: np.ndarray, current_ma_values: np.ndarray) -> float:
    if len(time_s) < 2:
        return 0.0
    return float(np.trapezoid(current_ma_values, time_s) * 1000.0)


def robust_sigma(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) < 2:
        return 0.0
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    if mad > 0:
        return 1.4826 * mad
    return float(np.std(finite, ddof=1))


def fit_exponential_tail(
    relative_s: np.ndarray,
    smoothed_ma: np.ndarray,
    peak_time_s: float,
    peak_ma: float,
    integration_end_s: float,
    baseline_noise_ma: float,
) -> TailFit | None:
    if not np.isfinite(peak_time_s) or not np.isfinite(peak_ma) or peak_ma == 0:
        return None
    if peak_time_s >= integration_end_s:
        return None

    sign = 1.0 if peak_ma > 0 else -1.0
    tail_mask = (
        (relative_s >= peak_time_s)
        & (relative_s <= integration_end_s)
        & np.isfinite(smoothed_ma)
    )
    if not np.any(tail_mask):
        return None

    tail_relative_s = relative_s[tail_mask]
    tail_t = tail_relative_s - peak_time_s
    signed_current_ma = sign * smoothed_ma[tail_mask]

    threshold_ma = max(3.0 * baseline_noise_ma, abs(peak_ma) * 0.03)
    fit_mask = signed_current_ma > threshold_ma
    if np.count_nonzero(fit_mask) < 20:
        return None

    fit_t = tail_t[fit_mask]
    fit_y = signed_current_ma[fit_mask]
    if len(fit_t) < 2 or float(np.ptp(fit_t)) <= 0:
        return None

    slope, intercept = np.polyfit(fit_t, np.log(fit_y), 1)
    if slope >= 0:
        return None

    tau_s = float(-1.0 / slope)
    magnitude_mA = float(np.exp(intercept))
    if tau_s <= 0 or not np.isfinite(tau_s) or not np.isfinite(magnitude_mA):
        return None
    if magnitude_mA > abs(peak_ma) * 1.5:
        return None

    predicted_log = slope * fit_t + intercept
    actual_log = np.log(fit_y)
    total_variance = float(np.sum((actual_log - np.mean(actual_log)) ** 2))
    residual = float(np.sum((actual_log - predicted_log) ** 2))
    r2_log = 1.0 - residual / total_variance if total_variance > 0 else float("nan")

    fit_relative_s = np.linspace(peak_time_s, integration_end_s, 1000)
    fit_current_ma = sign * magnitude_mA * np.exp(-(fit_relative_s - peak_time_s) / tau_s)
    fit_charge_uc = cumulative_trapezoid_uc(fit_relative_s - peak_time_s, fit_current_ma)

    return TailFit(
        relative_s=fit_relative_s,
        current_ma=fit_current_ma,
        charge_uc=fit_charge_uc,
        amplitude_mA=sign * magnitude_mA,
        tau_s=tau_s,
        charge_uC=float(fit_charge_uc[-1]),
        r2_log=r2_log,
        sample_count=int(np.count_nonzero(fit_mask)),
    )


def time_tag(edge_time_s: float) -> str:
    rounded = f"{edge_time_s:.6g}".replace("-", "m").replace(".", "p")
    return f"{rounded}s"


def analyze_event(
    data: pd.DataFrame,
    event: EdgeEvent,
    output_dir: Path,
    csv_stem: str,
    args: argparse.Namespace,
) -> dict:
    window_start = event.edge_time_s - args.window_before
    window_end = event.edge_time_s + args.window_after
    window = data[(data["time"] >= window_start) & (data["time"] <= window_end)].copy()
    if window.empty:
        raise RuntimeError(f"No waveform rows found around relay edge at {event.edge_time_s:g} s")

    time_s = window["time"].to_numpy(dtype=float)
    relative_s = time_s - event.edge_time_s
    current_values_ma = current_ma(window, args.shunt_ohms, args.amplifier_gain)
    voltage_values = window["ch1_voltage"].to_numpy(dtype=float)

    baseline_mask = (
        (relative_s >= args.baseline_start)
        & (relative_s <= args.baseline_end)
        & np.isfinite(current_values_ma)
    )
    if not np.any(baseline_mask):
        raise RuntimeError(
            f"No baseline samples for relay edge at {event.edge_time_s:g} s"
        )

    baseline_ma = float(np.median(current_values_ma[baseline_mask]))
    corrected_ma = current_values_ma - baseline_ma

    integration_mask = (
        (relative_s >= args.integrate_start)
        & (relative_s <= args.integrate_end)
        & np.isfinite(corrected_ma)
    )
    integration_relative_s = relative_s[integration_mask]
    integration_current_ma = corrected_ma[integration_mask]
    if len(integration_relative_s) < 2:
        raise RuntimeError(
            f"Not enough integration samples for relay edge at {event.edge_time_s:g} s"
        )

    cumulative_charge_uc = cumulative_trapezoid_uc(
        integration_relative_s,
        integration_current_ma,
    )
    net_charge_uc = float(cumulative_charge_uc[-1])
    positive_charge_uc = integrate_uc(
        integration_relative_s,
        np.clip(integration_current_ma, 0.0, None),
    )
    negative_charge_uc = integrate_uc(
        integration_relative_s,
        np.clip(integration_current_ma, None, 0.0),
    )

    min_index = int(np.argmin(integration_current_ma))
    max_index = int(np.argmax(integration_current_ma))
    peak_min_ma = float(integration_current_ma[min_index])
    peak_max_ma = float(integration_current_ma[max_index])
    peak_min_time_s = float(integration_relative_s[min_index])
    peak_max_time_s = float(integration_relative_s[max_index])
    if abs(peak_min_ma) >= abs(peak_max_ma):
        peak_abs_ma = peak_min_ma
        peak_abs_time_s = peak_min_time_s
    else:
        peak_abs_ma = peak_max_ma
        peak_abs_time_s = peak_max_time_s

    sample_interval_s = float(np.median(np.diff(time_s))) if len(time_s) > 1 else 0.0
    smoothed_ma = centered_rolling_median(
        corrected_ma,
        sample_interval_s,
        args.smooth_ms,
    )
    baseline_noise_ma = robust_sigma(corrected_ma[baseline_mask])
    before_peak_mask = integration_relative_s <= peak_abs_time_s
    after_peak_mask = integration_relative_s >= peak_abs_time_s
    charge_before_abs_peak_uc = integrate_uc(
        integration_relative_s[before_peak_mask],
        integration_current_ma[before_peak_mask],
    )
    charge_after_abs_peak_uc = integrate_uc(
        integration_relative_s[after_peak_mask],
        integration_current_ma[after_peak_mask],
    )
    tail_fit = fit_exponential_tail(
        relative_s=relative_s,
        smoothed_ma=smoothed_ma,
        peak_time_s=peak_abs_time_s,
        peak_ma=peak_abs_ma,
        integration_end_s=args.integrate_end,
        baseline_noise_ma=baseline_noise_ma,
    )

    output_path = (
        output_dir
        / f"{csv_stem}_edge_{event.index:02d}_{event.relay_channel.lower()}_"
        f"{event.transition}_{time_tag(event.edge_time_s)}.svg"
    )
    plot_event(
        output_path=output_path,
        event=event,
        relative_s=relative_s,
        voltage_values=voltage_values,
        corrected_ma=corrected_ma,
        smoothed_ma=smoothed_ma,
        integration_relative_s=integration_relative_s,
        cumulative_charge_uc=cumulative_charge_uc,
        peak_min_time_s=peak_min_time_s,
        peak_min_ma=peak_min_ma,
        peak_max_time_s=peak_max_time_s,
        peak_max_ma=peak_max_ma,
        peak_abs_time_s=peak_abs_time_s,
        peak_abs_ma=peak_abs_ma,
        net_charge_uc=net_charge_uc,
        negative_charge_uc=negative_charge_uc,
        positive_charge_uc=positive_charge_uc,
        charge_before_abs_peak_uc=charge_before_abs_peak_uc,
        tail_fit=tail_fit,
        args=args,
    )

    return {
        "event_index": event.index,
        "relay_channel": event.relay_channel,
        "transition": event.transition,
        "edge_time_s": event.edge_time_s,
        "window_start_s": window_start,
        "window_end_s": window_end,
        "baseline_start_relative_s": args.baseline_start,
        "baseline_end_relative_s": args.baseline_end,
        "baseline_mA": baseline_ma,
        "baseline_noise_mA": baseline_noise_ma,
        "baseline_sample_count": int(np.count_nonzero(baseline_mask)),
        "integrate_start_relative_s": args.integrate_start,
        "integrate_end_relative_s": args.integrate_end,
        "integration_sample_count": int(np.count_nonzero(integration_mask)),
        "net_charge_uC": net_charge_uc,
        "positive_charge_uC": positive_charge_uc,
        "negative_charge_uC": negative_charge_uc,
        "charge_before_abs_peak_uC": charge_before_abs_peak_uc,
        "charge_after_abs_peak_uC": charge_after_abs_peak_uc,
        "peak_min_mA": peak_min_ma,
        "peak_min_relative_s": peak_min_time_s,
        "peak_max_mA": peak_max_ma,
        "peak_max_relative_s": peak_max_time_s,
        "peak_abs_signed_mA": peak_abs_ma,
        "peak_abs_relative_s": peak_abs_time_s,
        "tail_fit_amplitude_mA": tail_fit.amplitude_mA if tail_fit else "",
        "tail_fit_tau_s": tail_fit.tau_s if tail_fit else "",
        "tail_fit_charge_uC": tail_fit.charge_uC if tail_fit else "",
        "tail_fit_r2_log": tail_fit.r2_log if tail_fit else "",
        "tail_fit_sample_count": tail_fit.sample_count if tail_fit else 0,
        "smooth_ms": args.smooth_ms,
        "shunt_ohms": args.shunt_ohms,
        "amplifier_gain": args.amplifier_gain,
        "svg_path": str(output_path),
    }


def plot_event(
    output_path: Path,
    event: EdgeEvent,
    relative_s: np.ndarray,
    voltage_values: np.ndarray,
    corrected_ma: np.ndarray,
    smoothed_ma: np.ndarray,
    integration_relative_s: np.ndarray,
    cumulative_charge_uc: np.ndarray,
    peak_min_time_s: float,
    peak_min_ma: float,
    peak_max_time_s: float,
    peak_max_ma: float,
    peak_abs_time_s: float,
    peak_abs_ma: float,
    net_charge_uc: float,
    negative_charge_uc: float,
    positive_charge_uc: float,
    charge_before_abs_peak_uc: float,
    tail_fit: TailFit | None,
    args: argparse.Namespace,
) -> None:
    fig, (voltage_ax, current_ax, charge_ax) = plt.subplots(
        nrows=3,
        ncols=1,
        sharex=True,
        figsize=(11, 8),
        constrained_layout=True,
    )

    for axis in (voltage_ax, current_ax, charge_ax):
        axis.axvline(0.0, color="#333333", linewidth=0.9, linestyle="--", alpha=0.8)
        axis.axvspan(
            args.baseline_start,
            args.baseline_end,
            color="#6b7280",
            alpha=0.12,
            linewidth=0,
        )
        axis.axvspan(
            args.integrate_start,
            args.integrate_end,
            color="#f59e0b",
            alpha=0.08,
            linewidth=0,
        )
        axis.grid(True, color="#d9d9d9", linewidth=0.7, alpha=0.8)

    voltage_ax.plot(relative_s, voltage_values, color="#1f77b4", linewidth=1.0)
    voltage_ax.set_ylabel("CH1 voltage (V)")

    current_ax.plot(
        relative_s,
        corrected_ma,
        color="#9ca3af",
        linewidth=0.55,
        alpha=0.7,
        label="raw baseline-corrected",
    )
    current_ax.plot(
        relative_s,
        smoothed_ma,
        color="#d62728",
        linewidth=1.3,
        label=f"{args.smooth_ms:g} ms rolling median",
    )
    if tail_fit:
        current_ax.plot(
            tail_fit.relative_s,
            tail_fit.current_ma,
            color="#111827",
            linewidth=1.2,
            linestyle="--",
            label=f"exp tail fit, tau={tail_fit.tau_s:.3g}s",
        )
    current_ax.axvline(
        peak_abs_time_s,
        color="#d97706",
        linewidth=0.9,
        linestyle=":",
        alpha=0.9,
        label="dominant raw peak",
    )
    current_ax.scatter(
        [peak_min_time_s, peak_max_time_s],
        [peak_min_ma, peak_max_ma],
        color=["#7f1d1d", "#065f46"],
        s=28,
        zorder=4,
        label="raw extrema",
    )
    current_ax.axhline(0.0, color="#111827", linewidth=0.8, alpha=0.7)
    current_ax.set_ylabel("Current (mA)")
    current_ax.legend(loc="upper right", fontsize=8)

    charge_ax.plot(
        integration_relative_s,
        cumulative_charge_uc,
        color="#6f42c1",
        linewidth=1.4,
        label="raw Q from relay edge",
    )
    charge_ax.axvline(
        peak_abs_time_s,
        color="#d97706",
        linewidth=0.9,
        linestyle=":",
        alpha=0.9,
    )
    if tail_fit:
        charge_ax.plot(
            tail_fit.relative_s,
            tail_fit.charge_uc,
            color="#0f766e",
            linewidth=1.3,
            linestyle="--",
            label="fit Q from peak",
        )
    charge_ax.axhline(0.0, color="#111827", linewidth=0.8, alpha=0.7)
    charge_ax.set_ylabel("Charge (uC)")
    charge_ax.set_xlabel("Time relative to relay edge (s)")
    charge_ax.legend(loc="best", fontsize=8)

    fit_text = f", fit {tail_fit.charge_uC:.3g} uC" if tail_fit else ""
    title = (
        f"{event.relay_channel} {event.transition} at {event.edge_time_s:g} s: "
        f"net {net_charge_uc:.3g} uC, neg {negative_charge_uc:.3g} uC, "
        f"pos {positive_charge_uc:.3g} uC, pre-peak {charge_before_abs_peak_uc:.3g} uC"
        f"{fit_text}"
    )
    fig.suptitle(title)
    fig.savefig(output_path, format="svg")
    plt.close(fig)


def write_summary(summary_path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        raise RuntimeError("No analysis rows to write")
    with summary_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        waveform_path = resolve_waveform_path(args.input_path)
        session_dir = waveform_path.parent
        config_path = args.config or session_dir / "config.json"
        output_dir = args.output_dir or session_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        events = load_events(config_path, args.edge_time)
        data = load_waveform(waveform_path)
        rows = [
            analyze_event(
                data=data,
                event=event,
                output_dir=output_dir,
                csv_stem=waveform_path.stem,
                args=args,
            )
            for event in events
        ]

        summary_path = output_dir / f"{waveform_path.stem}_edge_charge_summary.csv"
        write_summary(summary_path, rows)
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {summary_path}")
    for row in rows:
        print(f"Wrote {row['svg_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
