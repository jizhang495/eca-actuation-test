#!/usr/bin/env python3
"""Model whole-run current and charge transfer from waveform CSV data."""

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
    from scipy.optimize import least_squares
except ImportError as exc:
    print(
        "error: missing analysis dependency. Run with the project virtualenv "
        "or install matplotlib, numpy, pandas, and scipy.",
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


@dataclass
class EdgeFit:
    event: EdgeEvent
    accepted: bool
    model: str
    reason: str
    baseline_mA: float
    baseline_noise_mA: float
    peak_relative_s: float
    peak_signed_mA: float
    amplitude_total_mA: float
    fast_fraction: float
    tau_fast_s: float
    tau_slow_s: float
    offset_signed_mA: float
    bic_single: float
    bic_dual: float
    fit_sample_count: int
    prepeak_charge_uC: float
    exp_charge_uC: float
    offset_charge_uC: float
    transient_charge_uC: float
    window_model_charge_uC: float
    raw_window_charge_uC: float
    median_window_charge_uC: float
    fit_relative_s: np.ndarray
    fit_current_corrected_mA: np.ndarray
    fit_binned_relative_s: np.ndarray
    fit_binned_corrected_mA: np.ndarray
    window_relative_s: np.ndarray
    window_smoothed_corrected_mA: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a whole-run charge-transfer model. Flat regions use "
            "median-binned current; relay-edge windows are replaced by bounded "
            "dual-exponential fits."
        )
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Session directory, moku_waveform.csv, or oscilloscope_waveform.csv.",
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
        "--zero-baseline-start",
        type=float,
        default=0.0,
        help=(
            "Start of whole-run zero-current baseline window in seconds. "
            "Defaults to 0."
        ),
    )
    parser.add_argument(
        "--zero-baseline-end",
        type=float,
        default=20.0,
        help=(
            "End of whole-run zero-current baseline window in seconds. "
            "Defaults to 20, within the initial 0 V stage of the 750 s preset."
        ),
    )
    parser.add_argument(
        "--disable-zero-baseline",
        action="store_true",
        help="Do not subtract a whole-run zero-current baseline before modeling.",
    )
    parser.add_argument(
        "--baseline-start",
        type=float,
        default=-0.2,
        help="Baseline window start relative to each edge. Defaults to -0.2.",
    )
    parser.add_argument(
        "--baseline-end",
        type=float,
        default=-0.02,
        help="Baseline window end relative to each edge. Defaults to -0.02.",
    )
    parser.add_argument(
        "--fit-end",
        type=float,
        default=1.0,
        help="Seconds after each edge to model with the edge fit. Defaults to 1.0.",
    )
    parser.add_argument(
        "--peak-search-end",
        type=float,
        default=0.6,
        help="Seconds after each edge to search for the dominant peak. Defaults to 0.6.",
    )
    parser.add_argument(
        "--edge-smooth-ms",
        type=float,
        default=3.0,
        help="Rolling-median width for local edge fitting. Defaults to 3 ms.",
    )
    parser.add_argument(
        "--fit-bin-ms",
        type=float,
        default=1.0,
        help="Median bin width for fitting tail data. Defaults to 1 ms.",
    )
    parser.add_argument(
        "--flat-bin-ms",
        type=float,
        default=200.0,
        help="Median bin width for flat-region model. Defaults to 200 ms.",
    )
    parser.add_argument(
        "--output-dt-ms",
        type=float,
        default=1.0,
        help="Time step for output charge trace. Defaults to 1 ms.",
    )
    parser.add_argument(
        "--edge-exclude-before",
        type=float,
        default=0.02,
        help=(
            "Seconds before each edge to exclude from the flat-region median. "
            "Defaults to 0.02."
        ),
    )
    parser.add_argument(
        "--bic-threshold",
        type=float,
        default=10.0,
        help=(
            "Require dual exponential BIC to beat single exponential by this "
            "amount. Defaults to 10."
        ),
    )
    parser.add_argument(
        "--plot-max-points",
        type=int,
        default=50000,
        help="Maximum points per plotted overview trace. Defaults to 50000.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.shunt_ohms <= 0:
        raise RuntimeError("--shunt-ohms must be greater than 0")
    if args.amplifier_gain <= 0:
        raise RuntimeError("--amplifier-gain must be greater than 0")
    if not args.disable_zero_baseline and args.zero_baseline_start >= args.zero_baseline_end:
        raise RuntimeError("--zero-baseline-start must be less than --zero-baseline-end")
    if args.baseline_start >= args.baseline_end:
        raise RuntimeError("--baseline-start must be less than --baseline-end")
    if args.fit_end <= 0:
        raise RuntimeError("--fit-end must be greater than 0")
    if args.peak_search_end <= 0 or args.peak_search_end > args.fit_end:
        raise RuntimeError("--peak-search-end must be in (0, --fit-end]")
    if args.edge_smooth_ms <= 0:
        raise RuntimeError("--edge-smooth-ms must be greater than 0")
    if args.fit_bin_ms <= 0:
        raise RuntimeError("--fit-bin-ms must be greater than 0")
    if args.flat_bin_ms <= 0:
        raise RuntimeError("--flat-bin-ms must be greater than 0")
    if args.output_dt_ms <= 0:
        raise RuntimeError("--output-dt-ms must be greater than 0")
    if args.plot_max_points < 0:
        raise RuntimeError("--plot-max-points must be non-negative")


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
    # Required columns plus optional differential / pre-computed current columns.
    # SR551 sessions export the correct differential current as ``current_mA`` and
    # carry ``ch3_voltage``; raw-shunt/oscilloscope sessions have neither.
    header = pd.read_csv(csv_path, nrows=0).columns
    optional = [c for c in ("ch3_voltage", "current_mA") if c in header]
    columns = list(WAVEFORM_COLUMNS) + optional
    data = pd.read_csv(csv_path, usecols=columns)
    for column in columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=list(WAVEFORM_COLUMNS)).sort_values("time")
    if data.empty:
        raise RuntimeError(f"Waveform CSV contains no plottable rows: {csv_path}")
    return data.reset_index(drop=True)


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
                events.append(EdgeEvent(0, relay_channel, "close", float(start_time)))
            if state == "closed" and end_time is not None:
                events.append(EdgeEvent(0, relay_channel, "open", float(end_time)))

    events = sorted(events, key=lambda event: event.edge_time_s)
    if not events:
        raise RuntimeError(f"No relay edge events found in {config_path}")
    return [
        EdgeEvent(
            index=index + 1,
            relay_channel=event.relay_channel,
            transition=event.transition,
            edge_time_s=event.edge_time_s,
        )
        for index, event in enumerate(events)
    ]


def current_ma(
    data: pd.DataFrame, shunt_ohms: float, amplifier_gain: float
) -> tuple[np.ndarray, str]:
    """Select the current channel and return (current_mA, source_label).

    Priority:
    1. exported ``current_mA`` column (acquisition already applied the SR551
       differential + gain) -- the correct source for SR551 sessions;
    2. SR551 differential ``(ch2 - ch3)/(shunt*gain)`` if ``ch3_voltage`` is
       present but ``current_mA`` is not;
    3. raw single-ended shunt ``ch2/(shunt*gain)`` (old Moku / oscilloscope).
    """
    if "current_mA" in data.columns and data["current_mA"].notna().any():
        return data["current_mA"].to_numpy(dtype=float), "exported current_mA (SR551 differential)"
    if "ch3_voltage" in data.columns and data["ch3_voltage"].notna().any():
        diff = (data["ch2_voltage"].to_numpy(dtype=float) - data["ch3_voltage"].to_numpy(dtype=float))
        return diff / (shunt_ohms * amplifier_gain) * 1000.0, f"(ch2-ch3)/({shunt_ohms:g}*{amplifier_gain:g}) [SR551 differential]"
    return (
        data["ch2_voltage"].to_numpy(dtype=float) / (shunt_ohms * amplifier_gain) * 1000.0,
        f"ch2/({shunt_ohms:g}*{amplifier_gain:g}) [raw shunt]",
    )


def estimate_zero_baseline_mA(
    time_s: np.ndarray,
    current_values_ma: np.ndarray,
    args: argparse.Namespace,
) -> float:
    if args.disable_zero_baseline:
        return 0.0

    mask = (
        (time_s >= args.zero_baseline_start)
        & (time_s <= args.zero_baseline_end)
        & np.isfinite(current_values_ma)
    )
    if np.count_nonzero(mask) < 2:
        raise RuntimeError(
            "Not enough samples in the zero-current baseline window; "
            "adjust --zero-baseline-start/--zero-baseline-end or pass "
            "--disable-zero-baseline"
        )
    return float(np.median(current_values_ma[mask]))


def robust_sigma(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) < 2:
        return 0.0
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    if mad > 0:
        return 1.4826 * mad
    return float(np.std(finite, ddof=1))


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


def bic(residual: np.ndarray, parameter_count: int) -> float:
    n = len(residual)
    if n <= parameter_count:
        return float("inf")
    sse = max(float(np.sum(residual * residual)), np.finfo(float).tiny)
    return float(n * np.log(sse / n) + parameter_count * np.log(n))


def transition_sign(event: EdgeEvent) -> float:
    if event.transition == "close":
        return 1.0
    if event.transition == "open":
        return -1.0
    return 0.0


def median_bin_tail(
    relative_s: np.ndarray,
    corrected_ma: np.ndarray,
    peak_relative_s: float,
    fit_end_s: float,
    bin_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    mask = (
        (relative_s >= peak_relative_s)
        & (relative_s <= fit_end_s)
        & np.isfinite(corrected_ma)
    )
    if not np.any(mask):
        return np.array([], dtype=float), np.array([], dtype=float)

    t = relative_s[mask] - peak_relative_s
    y = corrected_ma[mask]
    bin_width_s = bin_ms / 1000.0
    bins = np.floor(t / bin_width_s).astype(np.int64)
    frame = pd.DataFrame({"bin": bins, "t": t, "y": y})
    binned = frame.groupby("bin", sort=True).agg(t=("t", "median"), y=("y", "median"))
    return binned["t"].to_numpy(dtype=float), binned["y"].to_numpy(dtype=float)


def dual_exp_signed(t: np.ndarray, amplitude_total: float, fast_fraction: float, tau_fast: float, tau_slow: float, offset: float) -> np.ndarray:
    fast_fraction = float(np.clip(fast_fraction, 0.0, 1.0))
    return (
        amplitude_total
        * (
            fast_fraction * np.exp(-t / tau_fast)
            + (1.0 - fast_fraction) * np.exp(-t / tau_slow)
        )
        + offset
    )


def single_exp_signed(t: np.ndarray, amplitude: float, tau: float, offset: float) -> np.ndarray:
    return amplitude * np.exp(-t / tau) + offset


def fit_edge_event(
    data: pd.DataFrame,
    current_values_ma: np.ndarray,
    event: EdgeEvent,
    args: argparse.Namespace,
) -> EdgeFit:
    sign = transition_sign(event)
    window_start = event.edge_time_s + min(args.baseline_start, -args.edge_exclude_before)
    window_end = event.edge_time_s + args.fit_end
    window_mask = (data["time"].to_numpy(dtype=float) >= window_start) & (
        data["time"].to_numpy(dtype=float) <= window_end
    )
    if not np.any(window_mask):
        return empty_edge_fit(event, "no waveform rows in edge window")

    time_s = data["time"].to_numpy(dtype=float)[window_mask]
    relative_s = time_s - event.edge_time_s
    edge_current_ma = current_values_ma[window_mask]
    sample_interval_s = float(np.median(np.diff(time_s))) if len(time_s) > 1 else 0.0

    baseline_mask = (
        (relative_s >= args.baseline_start)
        & (relative_s <= args.baseline_end)
        & np.isfinite(edge_current_ma)
    )
    if np.count_nonzero(baseline_mask) < 2:
        return empty_edge_fit(event, "not enough baseline samples")

    baseline_mA = float(np.median(edge_current_ma[baseline_mask]))
    corrected_ma = edge_current_ma - baseline_mA
    baseline_noise_mA = robust_sigma(corrected_ma[baseline_mask])
    smoothed_corrected_ma = centered_rolling_median(
        corrected_ma,
        sample_interval_s,
        args.edge_smooth_ms,
    )

    search_mask = (
        (relative_s >= 0.0)
        & (relative_s <= args.peak_search_end)
        & np.isfinite(smoothed_corrected_ma)
    )
    if np.count_nonzero(search_mask) < 2:
        return empty_edge_fit(event, "not enough peak-search samples")

    search_indices = np.flatnonzero(search_mask)
    if sign == 0.0:
        local_peak_index = int(np.argmax(np.abs(smoothed_corrected_ma[search_mask])))
        peak_signed_mA = float(smoothed_corrected_ma[search_indices[local_peak_index]])
        sign = 1.0 if peak_signed_mA >= 0 else -1.0
    else:
        local_peak_index = int(np.argmax(sign * smoothed_corrected_ma[search_mask]))
        peak_signed_mA = float(smoothed_corrected_ma[search_indices[local_peak_index]])

    peak_index = int(search_indices[local_peak_index])
    peak_relative_s = float(relative_s[peak_index])
    peak_magnitude_mA = abs(peak_signed_mA)
    min_peak_mA = max(3.0 * baseline_noise_mA, 0.005)
    if peak_magnitude_mA < min_peak_mA:
        return empty_edge_fit(
            event,
            f"peak below threshold ({peak_magnitude_mA:.4g} mA < {min_peak_mA:.4g} mA)",
            baseline_mA=baseline_mA,
            baseline_noise_mA=baseline_noise_mA,
            peak_relative_s=peak_relative_s,
            peak_signed_mA=peak_signed_mA,
            relative_s=relative_s,
            smoothed_corrected_ma=smoothed_corrected_ma,
        )

    fit_t, fit_y_corrected = median_bin_tail(
        relative_s,
        corrected_ma,
        peak_relative_s,
        args.fit_end,
        args.fit_bin_ms,
    )
    finite_fit = np.isfinite(fit_t) & np.isfinite(fit_y_corrected)
    fit_t = fit_t[finite_fit]
    fit_y_corrected = fit_y_corrected[finite_fit]
    fit_y_signed = sign * fit_y_corrected
    if len(fit_t) < 20:
        return empty_edge_fit(
            event,
            "not enough binned tail samples",
            baseline_mA=baseline_mA,
            baseline_noise_mA=baseline_noise_mA,
            peak_relative_s=peak_relative_s,
            peak_signed_mA=peak_signed_mA,
            relative_s=relative_s,
            smoothed_corrected_ma=smoothed_corrected_ma,
            fit_t=fit_t,
            fit_y_corrected=fit_y_corrected,
        )

    noise_scale = max(baseline_noise_mA, 0.001)
    offset_bound_mA = max(3.0 * baseline_noise_mA, 0.05 * peak_magnitude_mA, 0.003)
    amplitude_bound_mA = max(2.0 * peak_magnitude_mA, 0.02)

    def single_residual(params: np.ndarray) -> np.ndarray:
        amplitude, tau, offset = params
        return single_exp_signed(fit_t, amplitude, tau, offset) - fit_y_signed

    single_fit = least_squares(
        single_residual,
        x0=np.array([min(peak_magnitude_mA, amplitude_bound_mA), 0.08, 0.0]),
        bounds=(
            np.array([0.0, 0.003, -offset_bound_mA]),
            np.array([amplitude_bound_mA, 1.5, offset_bound_mA]),
        ),
        loss="soft_l1",
        f_scale=noise_scale,
        max_nfev=20000,
    )

    def dual_residual(params: np.ndarray) -> np.ndarray:
        amplitude_total, fast_fraction, tau_fast, tau_slow, offset = params
        return (
            dual_exp_signed(
                fit_t,
                amplitude_total,
                fast_fraction,
                tau_fast,
                tau_slow,
                offset,
            )
            - fit_y_signed
        )

    dual_fit = least_squares(
        dual_residual,
        x0=np.array(
            [
                min(peak_magnitude_mA, amplitude_bound_mA),
                0.65,
                0.008,
                0.08,
                0.0,
            ]
        ),
        bounds=(
            np.array([0.0, 0.0, 0.002, 0.025, -offset_bound_mA]),
            np.array([amplitude_bound_mA, 1.0, 0.03, 1.5, offset_bound_mA]),
        ),
        loss="soft_l1",
        f_scale=noise_scale,
        max_nfev=20000,
    )

    single_res = single_residual(single_fit.x)
    dual_res = dual_residual(dual_fit.x)
    bic_single = bic(single_res, 3)
    bic_dual = bic(dual_res, 5)

    use_dual = bool(
        dual_fit.success
        and np.isfinite(bic_dual)
        and bic_dual + args.bic_threshold < bic_single
    )

    if use_dual:
        amplitude_total, fast_fraction, tau_fast, tau_slow, offset_signed = dual_fit.x
        model = "dual"
        reason = "accepted"
    elif single_fit.success:
        amplitude_single, tau_single, offset_signed = single_fit.x
        amplitude_total = amplitude_single
        fast_fraction = 0.0
        tau_fast = 0.008
        tau_slow = tau_single
        model = "single"
        reason = "dual not supported by BIC"
    else:
        return empty_edge_fit(
            event,
            "single and dual fits failed",
            baseline_mA=baseline_mA,
            baseline_noise_mA=baseline_noise_mA,
            peak_relative_s=peak_relative_s,
            peak_signed_mA=peak_signed_mA,
            bic_single=bic_single,
            bic_dual=bic_dual,
            relative_s=relative_s,
            smoothed_corrected_ma=smoothed_corrected_ma,
            fit_t=fit_t,
            fit_y_corrected=fit_y_corrected,
        )

    tail_duration_s = max(0.0, args.fit_end - peak_relative_s)
    exp_charge_uC = float(
        sign
        * 1000.0
        * amplitude_total
        * (
            fast_fraction * tau_fast * (1.0 - math.exp(-tail_duration_s / tau_fast))
            + (1.0 - fast_fraction)
            * tau_slow
            * (1.0 - math.exp(-tail_duration_s / tau_slow))
        )
    )
    offset_charge_uC = float(sign * offset_signed * tail_duration_s * 1000.0)

    prepeak_mask = (
        (relative_s >= 0.0)
        & (relative_s <= peak_relative_s)
        & np.isfinite(smoothed_corrected_ma)
    )
    prepeak_charge_uC = integrate_uc(
        relative_s[prepeak_mask],
        smoothed_corrected_ma[prepeak_mask],
    )

    integration_mask = (
        (relative_s >= 0.0)
        & (relative_s <= args.fit_end)
        & np.isfinite(corrected_ma)
    )
    raw_window_charge_uC = integrate_uc(
        relative_s[integration_mask],
        corrected_ma[integration_mask],
    )
    median_window_charge_uC = integrate_uc(
        relative_s[integration_mask],
        smoothed_corrected_ma[integration_mask],
    )

    fit_relative_s = np.linspace(peak_relative_s, args.fit_end, 1000)
    fit_t_dense = fit_relative_s - peak_relative_s
    fit_current_corrected_mA = sign * dual_exp_signed(
        fit_t_dense,
        amplitude_total,
        fast_fraction,
        tau_fast,
        tau_slow,
        offset_signed,
    )

    transient_charge_uC = prepeak_charge_uC + exp_charge_uC
    window_model_charge_uC = transient_charge_uC + offset_charge_uC

    return EdgeFit(
        event=event,
        accepted=True,
        model=model,
        reason=reason,
        baseline_mA=baseline_mA,
        baseline_noise_mA=baseline_noise_mA,
        peak_relative_s=peak_relative_s,
        peak_signed_mA=peak_signed_mA,
        amplitude_total_mA=float(amplitude_total),
        fast_fraction=float(fast_fraction),
        tau_fast_s=float(tau_fast),
        tau_slow_s=float(tau_slow),
        offset_signed_mA=float(sign * offset_signed),
        bic_single=bic_single,
        bic_dual=bic_dual,
        fit_sample_count=len(fit_t),
        prepeak_charge_uC=prepeak_charge_uC,
        exp_charge_uC=exp_charge_uC,
        offset_charge_uC=offset_charge_uC,
        transient_charge_uC=transient_charge_uC,
        window_model_charge_uC=window_model_charge_uC,
        raw_window_charge_uC=raw_window_charge_uC,
        median_window_charge_uC=median_window_charge_uC,
        fit_relative_s=fit_relative_s,
        fit_current_corrected_mA=fit_current_corrected_mA,
        fit_binned_relative_s=fit_t + peak_relative_s,
        fit_binned_corrected_mA=fit_y_corrected,
        window_relative_s=relative_s,
        window_smoothed_corrected_mA=smoothed_corrected_ma,
    )


def empty_edge_fit(
    event: EdgeEvent,
    reason: str,
    baseline_mA: float = float("nan"),
    baseline_noise_mA: float = float("nan"),
    peak_relative_s: float = float("nan"),
    peak_signed_mA: float = float("nan"),
    bic_single: float = float("nan"),
    bic_dual: float = float("nan"),
    relative_s: np.ndarray | None = None,
    smoothed_corrected_ma: np.ndarray | None = None,
    fit_t: np.ndarray | None = None,
    fit_y_corrected: np.ndarray | None = None,
) -> EdgeFit:
    return EdgeFit(
        event=event,
        accepted=False,
        model="none",
        reason=reason,
        baseline_mA=baseline_mA,
        baseline_noise_mA=baseline_noise_mA,
        peak_relative_s=peak_relative_s,
        peak_signed_mA=peak_signed_mA,
        amplitude_total_mA=float("nan"),
        fast_fraction=float("nan"),
        tau_fast_s=float("nan"),
        tau_slow_s=float("nan"),
        offset_signed_mA=float("nan"),
        bic_single=bic_single,
        bic_dual=bic_dual,
        fit_sample_count=0 if fit_t is None else len(fit_t),
        prepeak_charge_uC=float("nan"),
        exp_charge_uC=float("nan"),
        offset_charge_uC=float("nan"),
        transient_charge_uC=float("nan"),
        window_model_charge_uC=float("nan"),
        raw_window_charge_uC=float("nan"),
        median_window_charge_uC=float("nan"),
        fit_relative_s=np.array([], dtype=float),
        fit_current_corrected_mA=np.array([], dtype=float),
        fit_binned_relative_s=np.array([], dtype=float) if fit_t is None else fit_t,
        fit_binned_corrected_mA=np.array([], dtype=float) if fit_y_corrected is None else fit_y_corrected,
        window_relative_s=np.array([], dtype=float) if relative_s is None else relative_s,
        window_smoothed_corrected_mA=(
            np.array([], dtype=float)
            if smoothed_corrected_ma is None
            else smoothed_corrected_ma
        ),
    )


def edge_exclusion_mask(
    time_s: np.ndarray,
    events: Iterable[EdgeEvent],
    exclude_before_s: float,
    exclude_after_s: float,
) -> np.ndarray:
    mask = np.zeros(len(time_s), dtype=bool)
    for event in events:
        mask |= (
            (time_s >= event.edge_time_s - exclude_before_s)
            & (time_s <= event.edge_time_s + exclude_after_s)
        )
    return mask


def flat_median_model(
    time_s: np.ndarray,
    current_values_ma: np.ndarray,
    events: Iterable[EdgeEvent],
    flat_bin_ms: float,
    exclude_before_s: float,
    exclude_after_s: float,
    grid_time_s: np.ndarray,
) -> np.ndarray:
    exclude = edge_exclusion_mask(time_s, events, exclude_before_s, exclude_after_s)
    valid = (~exclude) & np.isfinite(time_s) & np.isfinite(current_values_ma)
    if np.count_nonzero(valid) < 2:
        raise RuntimeError("Not enough non-edge samples for flat-region median model")

    t = time_s[valid]
    y = current_values_ma[valid]
    bin_width_s = flat_bin_ms / 1000.0
    t0 = float(time_s[0])
    bins = np.floor((t - t0) / bin_width_s).astype(np.int64)
    frame = pd.DataFrame({"bin": bins, "time": t, "current_mA": y})
    summary = frame.groupby("bin", sort=True).agg(
        time=("time", "median"),
        current_mA=("current_mA", "median"),
    )
    if len(summary) < 2:
        raise RuntimeError("Not enough flat-region median bins")

    return np.interp(
        grid_time_s,
        summary["time"].to_numpy(dtype=float),
        summary["current_mA"].to_numpy(dtype=float),
    )


def build_modeled_current(
    data: pd.DataFrame,
    current_values_ma: np.ndarray,
    fits: list[EdgeFit],
    args: argparse.Namespace,
) -> pd.DataFrame:
    time_s = data["time"].to_numpy(dtype=float)
    output_dt_s = args.output_dt_ms / 1000.0
    grid_time_s = np.arange(time_s[0], time_s[-1] + output_dt_s / 2.0, output_dt_s)
    grid_time_s = grid_time_s[grid_time_s <= time_s[-1]]

    model_current_mA = flat_median_model(
        time_s=time_s,
        current_values_ma=current_values_ma,
        events=[fit.event for fit in fits],
        flat_bin_ms=args.flat_bin_ms,
        exclude_before_s=args.edge_exclude_before,
        exclude_after_s=args.fit_end,
        grid_time_s=grid_time_s,
    )

    for fit in fits:
        if not fit.accepted:
            continue

        sign = transition_sign(fit.event)
        if sign == 0.0:
            sign = 1.0 if fit.peak_signed_mA >= 0 else -1.0

        event_mask = (
            (grid_time_s >= fit.event.edge_time_s)
            & (grid_time_s <= fit.event.edge_time_s + args.fit_end)
        )
        if not np.any(event_mask):
            continue

        rel = grid_time_s[event_mask] - fit.event.edge_time_s
        event_current = np.empty_like(rel)
        prepeak_mask = rel <= fit.peak_relative_s
        if np.any(prepeak_mask):
            event_current[prepeak_mask] = fit.baseline_mA + np.interp(
                rel[prepeak_mask],
                fit.window_relative_s,
                fit.window_smoothed_corrected_mA,
            )

        tail_mask = ~prepeak_mask
        if np.any(tail_mask):
            t_tail = rel[tail_mask] - fit.peak_relative_s
            event_current[tail_mask] = fit.baseline_mA + sign * dual_exp_signed(
                t_tail,
                fit.amplitude_total_mA,
                fit.fast_fraction,
                fit.tau_fast_s,
                fit.tau_slow_s,
                sign * fit.offset_signed_mA,
            )

        model_current_mA[event_mask] = event_current

    voltage_v = np.interp(
        grid_time_s,
        time_s,
        data["ch1_voltage"].to_numpy(dtype=float),
    )
    cumulative_charge_uC = cumulative_trapezoid_uc(grid_time_s, model_current_mA)
    cumulative_abs_charge_uC = cumulative_trapezoid_uc(
        grid_time_s,
        np.abs(model_current_mA),
    )

    return pd.DataFrame(
        {
            "time_s": grid_time_s,
            "voltage_V": voltage_v,
            "modeled_current_mA": model_current_mA,
            "cumulative_charge_uC": cumulative_charge_uC,
            "cumulative_abs_charge_uC": cumulative_abs_charge_uC,
        }
    )


def peak_preserving_downsample(
    x_values: np.ndarray,
    y_values: np.ndarray,
    max_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(x_values) & np.isfinite(y_values)
    x = x_values[valid]
    y = y_values[valid]
    if max_points <= 0 or len(x) <= max_points:
        return x, y

    bin_count = max(1, max_points // 2)
    edges = np.linspace(0, len(x), bin_count + 1, dtype=int)
    selected_indices: list[int] = []

    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        chunk = y[start:stop]
        local_min = int(np.argmin(chunk)) + start
        local_max = int(np.argmax(chunk)) + start
        selected_indices.extend(sorted({local_min, local_max}))

    selected = np.array(selected_indices, dtype=int)
    return x[selected], y[selected]


def plot_charge_transfer(
    output_path: Path,
    data: pd.DataFrame,
    current_values_ma: np.ndarray,
    modeled: pd.DataFrame,
    fits: list[EdgeFit],
    args: argparse.Namespace,
) -> None:
    raw_time = data["time"].to_numpy(dtype=float)
    raw_voltage = data["ch1_voltage"].to_numpy(dtype=float)
    voltage_x, voltage_y = peak_preserving_downsample(
        raw_time,
        raw_voltage,
        args.plot_max_points,
    )
    current_x, current_y = peak_preserving_downsample(
        raw_time,
        current_values_ma,
        args.plot_max_points,
    )
    model_x, model_current_y = peak_preserving_downsample(
        modeled["time_s"].to_numpy(dtype=float),
        modeled["modeled_current_mA"].to_numpy(dtype=float),
        args.plot_max_points,
    )

    fig, axes = plt.subplots(
        nrows=4,
        ncols=1,
        sharex=False,
        figsize=(12, 11),
        constrained_layout=True,
    )
    voltage_ax, current_ax, charge_ax, edge_ax = axes

    for axis in axes:
        axis.grid(True, color="#d9d9d9", linewidth=0.7, alpha=0.8)
        for fit in fits:
            axis.axvline(
                fit.event.edge_time_s,
                color="#8c8c8c",
                linewidth=0.55,
                alpha=0.45,
            )

    voltage_ax.plot(voltage_x, voltage_y, color="#1f77b4", linewidth=1.0)
    voltage_ax.set_ylabel("Voltage (V)")

    current_ax.plot(
        current_x,
        current_y,
        color="#b8b8b8",
        linewidth=0.45,
        alpha=0.65,
        label="raw current",
    )
    current_ax.plot(
        model_x,
        model_current_y,
        color="#d62728",
        linewidth=1.1,
        label="median + fit model",
    )
    current_ax.axhline(0.0, color="#333333", linewidth=0.8, alpha=0.8)
    current_ax.set_ylabel("Current (mA)")
    current_ax.legend(loc="upper right", fontsize=8)

    charge_ax.plot(
        modeled["time_s"],
        modeled["cumulative_charge_uC"],
        color="#6f42c1",
        linewidth=1.4,
        label="signed cumulative charge",
    )
    charge_ax.plot(
        modeled["time_s"],
        modeled["cumulative_abs_charge_uC"],
        color="#0f766e",
        linewidth=1.1,
        label="absolute transferred charge",
    )
    charge_ax.axhline(0.0, color="#333333", linewidth=0.8, alpha=0.8)
    charge_ax.set_ylabel("Charge (uC)")
    charge_ax.legend(loc="best", fontsize=8)

    edge_times = [fit.event.edge_time_s for fit in fits]
    edge_charges = [
        fit.window_model_charge_uC if fit.accepted else 0.0
        for fit in fits
    ]
    edge_colors = ["#065f46" if charge >= 0 else "#7f1d1d" for charge in edge_charges]
    edge_ax.bar(edge_times, edge_charges, width=7.5, color=edge_colors, alpha=0.82)
    edge_ax.axhline(0.0, color="#333333", linewidth=0.8, alpha=0.8)
    edge_ax.set_ylabel("Edge Q (uC)")
    edge_ax.set_xlabel("Time since measurement t0 (s)")

    total_signed = float(modeled["cumulative_charge_uC"].iloc[-1])
    total_abs = float(modeled["cumulative_abs_charge_uC"].iloc[-1])
    fig.suptitle(
        f"Charge transfer model: signed {total_signed:.3g} uC, "
        f"absolute {total_abs:.3g} uC"
    )
    fig.savefig(output_path, format="svg")
    plt.close(fig)


def plot_edge_fit_overview(output_path: Path, fits: list[EdgeFit]) -> None:
    cols = 2
    rows = int(math.ceil(len(fits) / cols))
    fig, axes = plt.subplots(
        nrows=rows,
        ncols=cols,
        figsize=(12, max(3.0, rows * 2.35)),
        constrained_layout=True,
    )
    axes_array = np.atleast_1d(axes).reshape(rows, cols)

    for axis in axes_array.ravel():
        axis.axis("off")

    for fit, axis in zip(fits, axes_array.ravel()):
        axis.axis("on")
        if len(fit.fit_binned_relative_s) > 0:
            axis.scatter(
                fit.fit_binned_relative_s,
                fit.fit_binned_corrected_mA,
                color="#9ca3af",
                s=7,
                alpha=0.75,
                label="1 ms median",
            )
        if len(fit.window_relative_s) > 0:
            mask = (fit.window_relative_s >= 0.0) & (
                fit.window_relative_s <= fit.peak_relative_s
            )
            if np.any(mask):
                axis.plot(
                    fit.window_relative_s[mask],
                    fit.window_smoothed_corrected_mA[mask],
                    color="#d97706",
                    linewidth=1.0,
                    label="pre-peak median",
                )
        if fit.accepted:
            axis.plot(
                fit.fit_relative_s,
                fit.fit_current_corrected_mA,
                color="#111827",
                linewidth=1.2,
                label=f"{fit.model} fit",
            )
            title_suffix = (
                f"Q={fit.window_model_charge_uC:.2f}uC, "
                f"tf={fit.tau_fast_s*1000:.1f}ms, "
                f"ts={fit.tau_slow_s*1000:.0f}ms"
            )
        else:
            title_suffix = fit.reason
        axis.axhline(0.0, color="#333333", linewidth=0.7, alpha=0.8)
        axis.axvline(0.0, color="#333333", linewidth=0.7, linestyle="--", alpha=0.6)
        axis.axvline(
            fit.peak_relative_s,
            color="#d97706",
            linewidth=0.7,
            linestyle=":",
            alpha=0.9,
        )
        axis.grid(True, color="#d9d9d9", linewidth=0.6, alpha=0.8)
        axis.set_title(
            f"{fit.event.index}. {fit.event.transition} {fit.event.edge_time_s:g}s: {title_suffix}",
            fontsize=8,
        )
        axis.set_xlabel("Relative time (s)", fontsize=8)
        axis.set_ylabel("Current (mA)", fontsize=8)
        axis.tick_params(axis="both", labelsize=8)

    handles, labels = axes_array.ravel()[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", fontsize=8)
    fig.suptitle("Relay-edge fit diagnostics", fontsize=12)
    fig.savefig(output_path, format="svg")
    plt.close(fig)


def edge_fit_rows(fits: list[EdgeFit]) -> list[dict]:
    rows = []
    for fit in fits:
        rows.append(
            {
                "event_index": fit.event.index,
                "relay_channel": fit.event.relay_channel,
                "transition": fit.event.transition,
                "edge_time_s": fit.event.edge_time_s,
                "accepted": fit.accepted,
                "model": fit.model,
                "reason": fit.reason,
                "baseline_mA": fit.baseline_mA,
                "baseline_noise_mA": fit.baseline_noise_mA,
                "peak_relative_s": fit.peak_relative_s,
                "peak_signed_mA": fit.peak_signed_mA,
                "amplitude_total_mA": fit.amplitude_total_mA,
                "fast_fraction": fit.fast_fraction,
                "tau_fast_s": fit.tau_fast_s,
                "tau_slow_s": fit.tau_slow_s,
                "offset_signed_mA": fit.offset_signed_mA,
                "bic_single": fit.bic_single,
                "bic_dual": fit.bic_dual,
                "delta_bic_dual_minus_single": fit.bic_dual - fit.bic_single,
                "fit_sample_count": fit.fit_sample_count,
                "prepeak_charge_uC": fit.prepeak_charge_uC,
                "exp_charge_uC": fit.exp_charge_uC,
                "offset_charge_uC": fit.offset_charge_uC,
                "transient_charge_uC": fit.transient_charge_uC,
                "window_model_charge_uC": fit.window_model_charge_uC,
                "raw_window_charge_uC": fit.raw_window_charge_uC,
                "median_window_charge_uC": fit.median_window_charge_uC,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError(f"No rows to write: {path}")
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(
    path: Path,
    waveform_path: Path,
    modeled: pd.DataFrame,
    fits: list[EdgeFit],
    args: argparse.Namespace,
    zero_baseline_mA: float,
    current_source: str = "ch2 (raw shunt)",
) -> None:
    accepted = [fit for fit in fits if fit.accepted]
    rejected = [fit for fit in fits if not fit.accepted]
    total_signed = float(modeled["cumulative_charge_uC"].iloc[-1])
    total_abs = float(modeled["cumulative_abs_charge_uC"].iloc[-1])
    close_charge = sum(
        fit.window_model_charge_uC
        for fit in accepted
        if fit.event.transition == "close"
    )
    open_charge = sum(
        fit.window_model_charge_uC
        for fit in accepted
        if fit.event.transition == "open"
    )

    lines = [
        "# Charge Transfer Analysis",
        "",
        f"Waveform: `{waveform_path.name}`",
        "",
        "## Method",
        "",
        "- CH2 was converted to current using "
        f"Current source: {current_source}.",
        "- A whole-run zero-current baseline of "
        f"`{zero_baseline_mA:.6g} mA` was subtracted using "
        f"`{args.zero_baseline_start:g}-{args.zero_baseline_end:g} s`."
        if not args.disable_zero_baseline
        else "- Whole-run zero-current baseline subtraction was disabled.",
        f"- Flat regions use {args.flat_bin_ms:g} ms median bins, excluding "
        f"{args.edge_exclude_before:g} s before to {args.fit_end:g} s after each relay edge.",
        f"- Edge tails use {args.fit_bin_ms:g} ms median bins and bounded robust least-squares fits.",
        "- The preferred edge model is dual exponential when BIC supports it: "
        "`I = offset + A * (f exp(-t/tau_fast) + (1-f) exp(-t/tau_slow))`.",
        "- The plotted whole-run current replaces each edge window with the fitted edge model and uses the median model elsewhere.",
        "",
        "## Whole-Run Results",
        "",
        f"- Final signed cumulative charge: `{total_signed:.6g} uC`",
        f"- Final absolute transferred charge: `{total_abs:.6g} uC`",
        f"- Sum of accepted close-edge fitted window charge: `{close_charge:.6g} uC`",
        f"- Sum of accepted open-edge fitted window charge: `{open_charge:.6g} uC`",
        f"- Accepted edge fits: `{len(accepted)} / {len(fits)}`",
    ]
    if rejected:
        lines.extend(["", "Rejected edge fits:"])
        for fit in rejected:
            lines.append(
                f"- Event {fit.event.index} at {fit.event.edge_time_s:g} s: {fit.reason}"
            )

    lines.extend(
        [
            "",
            "## Edge Fits",
            "",
            "| Event | Transition | Edge s | Model | Window Q uC | Raw Q uC | Tau fast ms | Tau slow ms | Delta BIC |",
            "|---:|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for fit in fits:
        delta_bic = fit.bic_dual - fit.bic_single
        lines.append(
            f"| {fit.event.index} | {fit.event.transition} | "
            f"{fit.event.edge_time_s:g} | {fit.model} | "
            f"{fit.window_model_charge_uC:.3f} | {fit.raw_window_charge_uC:.3f} | "
            f"{fit.tau_fast_s * 1000.0:.3g} | {fit.tau_slow_s * 1000.0:.3g} | "
            f"{delta_bic:.3g} |"
        )

    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        waveform_path = resolve_waveform_path(args.input_path)
        session_dir = waveform_path.parent
        config_path = args.config or session_dir / "config.json"
        output_dir = args.output_dir or session_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        data = load_waveform(waveform_path)
        # Prefer the session config's sense parameters (used only for the SR551
        # differential fallback; the exported current_mA already has them applied).
        shunt_ohms, amplifier_gain = args.shunt_ohms, args.amplifier_gain
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                shunt_ohms = float(cfg.get("current_shunt_ohms") or shunt_ohms)
                amplifier_gain = float(cfg.get("current_amplifier_gain") or amplifier_gain)
            except Exception:
                pass
        raw_current_values, current_source = current_ma(data, shunt_ohms, amplifier_gain)
        print(f"current source: {current_source}")
        zero_baseline_mA = estimate_zero_baseline_mA(
            data["time"].to_numpy(dtype=float),
            raw_current_values,
            args,
        )
        current_values = raw_current_values - zero_baseline_mA
        events = load_events(config_path, args.edge_time)
        fits = [
            fit_edge_event(
                data=data,
                current_values_ma=current_values,
                event=event,
                args=args,
            )
            for event in events
        ]
        modeled = build_modeled_current(data, current_values, fits, args)

        stem = waveform_path.stem
        timeseries_path = output_dir / f"{stem}_charge_transfer_timeseries.csv"
        edge_summary_path = output_dir / f"{stem}_charge_transfer_edge_fits.csv"
        overview_path = output_dir / f"{stem}_charge_transfer.svg"
        edge_plot_path = output_dir / f"{stem}_charge_transfer_edge_fits.svg"
        summary_path = output_dir / f"{stem}_charge_transfer_summary.md"

        modeled.to_csv(timeseries_path, index=False)
        write_csv(edge_summary_path, edge_fit_rows(fits))
        plot_charge_transfer(
            output_path=overview_path,
            data=data,
            current_values_ma=current_values,
            modeled=modeled,
            fits=fits,
            args=args,
        )
        plot_edge_fit_overview(edge_plot_path, fits)
        write_summary(summary_path, waveform_path, modeled, fits, args, zero_baseline_mA, current_source)
    except (
        FileNotFoundError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        pd.errors.ParserError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {timeseries_path}")
    print(f"Wrote {edge_summary_path}")
    print(f"Wrote {overview_path}")
    print(f"Wrote {edge_plot_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
