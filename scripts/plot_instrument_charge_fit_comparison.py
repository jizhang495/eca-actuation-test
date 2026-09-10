#!/usr/bin/env python3
"""Compare charge-voltage fits for DMM, Tek oscilloscope, and Moku runs.

The three sessions use the same 750 s relay2 stepped-voltage protocol. To keep
the comparison cross-instrument, this script integrates the baseline-corrected
current in the first 1.0 s after each CH2 close edge. That window is short
enough to avoid long-window current-offset drift in the high-rate data, while
still being possible to estimate from the 10 Hz DMM stream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares


REPO = Path(__file__).resolve().parents[1]
SESS = REPO / "user-data" / "sessions"
OUT = REPO / "user-data" / "reports" / "figures"
SHUNT_OHMS = 330.0
INTEGRATION_SECONDS = 1.0
PEAK_DEMO_SESSION = "2026-05-22_12-45-22_step_voltage_relay2_750s_moku"
PEAK_DEMO_EVENT_INDEX = 9
TEK_PEAK_DEMO_SESSION = "2026-05-18_15-50-23_step_voltage_relay2_750s_oscilloscope"
TEK_PEAK_DEMO_EDGE_TIME_S = 250.0
DMM_REFERENCE_SESSION = "2026-05-06_13-20-47_step_voltage_relay2_750s"


@dataclass(frozen=True)
class SessionSpec:
    label: str
    session: str
    data_file: str
    time_column: str
    shunt_voltage_column: str
    baseline_start_s: float
    baseline_end_s: float
    color: str
    marker: str


SESSIONS = [
    SessionSpec(
        label="DMM 10 Hz",
        session="2026-05-06_13-20-47_step_voltage_relay2_750s",
        data_file="readings.csv",
        time_column="time",
        shunt_voltage_column="dmm2_voltage",
        baseline_start_s=-5.0,
        baseline_end_s=-1.0,
        color="#1f77b4",
        marker="o",
    ),
    SessionSpec(
        label="Tek oscilloscope",
        session="2026-05-18_15-50-23_step_voltage_relay2_750s_oscilloscope",
        data_file="oscilloscope_waveform.csv",
        time_column="time",
        shunt_voltage_column="ch2_voltage",
        baseline_start_s=-0.2,
        baseline_end_s=-0.02,
        color="#d62728",
        marker="s",
    ),
    SessionSpec(
        label="Moku:Pro",
        session="2026-05-22_12-45-22_step_voltage_relay2_750s_moku",
        data_file="moku_waveform.csv",
        time_column="time",
        shunt_voltage_column="ch2_voltage",
        baseline_start_s=-0.2,
        baseline_end_s=-0.02,
        color="#2ca02c",
        marker="^",
    ),
]


def voltage_at(config: dict, t_s: float) -> float:
    for stage in config["voltage_stages"]:
        if stage["start_time"] <= t_s < stage["end_time"]:
            return float(stage["voltage"])
    raise ValueError(f"No voltage stage found at t={t_s:g}s")


def load_rows(spec: SessionSpec) -> pd.DataFrame:
    session_dir = SESS / spec.session
    config = json.loads((session_dir / "config.json").read_text())
    frame = pd.read_csv(
        session_dir / spec.data_file,
        usecols=[spec.time_column, spec.shunt_voltage_column],
    )
    time_s = frame[spec.time_column].to_numpy(float)
    current_a = frame[spec.shunt_voltage_column].to_numpy(float) / SHUNT_OHMS

    rows: list[dict] = []
    for event_index, relay_stage in enumerate(config["relay_ch2_stages"], start=1):
        if relay_stage.get("state") != "closed":
            continue
        edge_s = float(relay_stage["start_time"])
        end_s = edge_s + INTEGRATION_SECONDS
        voltage = voltage_at(config, 0.5 * (edge_s + float(relay_stage["end_time"])))

        baseline_mask = (
            (time_s >= edge_s + spec.baseline_start_s)
            & (time_s < edge_s + spec.baseline_end_s)
        )
        integrate_mask = (time_s >= edge_s) & (time_s < end_s)
        if np.count_nonzero(baseline_mask) < 2 or np.count_nonzero(integrate_mask) < 2:
            charge_uc = np.nan
            baseline_ma = np.nan
            sample_count = int(np.count_nonzero(integrate_mask))
        else:
            baseline_a = float(np.median(current_a[baseline_mask]))
            charge_uc = float(
                np.trapezoid(current_a[integrate_mask] - baseline_a, time_s[integrate_mask])
                * 1e6
            )
            baseline_ma = baseline_a * 1000.0
            sample_count = int(np.count_nonzero(integrate_mask))

        rows.append(
            {
                "instrument": spec.label,
                "session": spec.session,
                "event_index": event_index,
                "edge_time_s": edge_s,
                "voltage_V": voltage,
                "charge_uC": charge_uc,
                "baseline_mA": baseline_ma,
                "integration_s": INTEGRATION_SECONDS,
                "integration_sample_count": sample_count,
                "branch": "up" if event_index <= 4 else "down",
                "method": (
                    "baseline-corrected current integral over first 1.0 s "
                    "after each CH2 close edge"
                ),
            }
        )

    return pd.DataFrame(rows)


def fit_rows(rows: pd.DataFrame) -> dict:
    clean = rows[np.isfinite(rows["charge_uC"])].copy()
    x = clean["voltage_V"].to_numpy(float)
    y = clean["charge_uC"].to_numpy(float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return {
        "instrument": str(clean["instrument"].iloc[0]),
        "slope_uC_per_V": float(slope),
        "intercept_uC": float(intercept),
        "r2": float(r2),
        "n": int(len(clean)),
    }


def plot(data: pd.DataFrame, fits: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.3, 5.6))
    volt_grid = np.linspace(0.0, 0.85, 200)

    for spec in SESSIONS:
        rows = data[data["instrument"] == spec.label]
        fit = fits[fits["instrument"] == spec.label].iloc[0]
        up = rows[rows["branch"] == "up"]
        down = rows[rows["branch"] == "down"]

        ax.scatter(
            up["voltage_V"],
            up["charge_uC"],
            s=58,
            marker=spec.marker,
            color=spec.color,
            edgecolor="white",
            linewidth=0.7,
            zorder=3,
            label=(
                f"{spec.label} data; fit {fit.slope_uC_per_V:.2f} uC/V, "
                f"R2={fit.r2:.2f}"
            ),
        )
        ax.scatter(
            down["voltage_V"],
            down["charge_uC"],
            s=58,
            marker=spec.marker,
            facecolor="none",
            edgecolor=spec.color,
            linewidth=1.5,
            zorder=3,
        )
        ax.plot(
            volt_grid,
            fit.slope_uC_per_V * volt_grid + fit.intercept_uC,
            color=spec.color,
            linewidth=1.8,
            alpha=0.9,
        )

    ax.set_title("Same 750 s relay protocol: first-second charge after CH2 close")
    ax.set_xlabel("Commanded voltage during relay-closed stage (V)")
    ax.set_ylabel("Baseline-corrected charge in first 1.0 s (uC)")
    ax.grid(True, alpha=0.28)
    ax.set_xlim(0.0, 0.86)
    ax.set_ylim(bottom=-0.4)
    ax.legend(loc="upper left", fontsize=8.5, frameon=True)
    ax.text(
        0.98,
        0.03,
        "Filled markers: up-sweep; open markers: down-sweep. "
        "DMM has only ~10 samples per 1 s window.",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8.5,
        color="#374151",
    )
    fig.tight_layout()
    fig.savefig(out_path.with_suffix(".png"), dpi=180)
    fig.savefig(out_path.with_suffix(".svg"))
    plt.close(fig)


def dual_exp_signed(
    t_s: np.ndarray,
    amplitude_total_mA: float,
    fast_fraction: float,
    tau_fast_s: float,
    tau_slow_s: float,
    offset_signed_mA: float,
) -> np.ndarray:
    return (
        amplitude_total_mA
        * (
            fast_fraction * np.exp(-t_s / tau_fast_s)
            + (1.0 - fast_fraction) * np.exp(-t_s / tau_slow_s)
        )
        + offset_signed_mA
    )


def fit_local_dual_exp(
    relative_s: np.ndarray,
    corrected_mA: np.ndarray,
    *,
    fit_end_s: float,
    peak_search_end_s: float = 0.2,
    fit_bin_s: float = 0.002,
) -> dict:
    search_mask = (
        (relative_s >= 0.0)
        & (relative_s <= peak_search_end_s)
        & np.isfinite(corrected_mA)
    )
    if np.count_nonzero(search_mask) < 5:
        raise RuntimeError("not enough samples to find local current peak")

    search_indices = np.flatnonzero(search_mask)
    peak_index = int(search_indices[np.argmax(corrected_mA[search_mask])])
    peak_relative_s = float(relative_s[peak_index])
    peak_mA = float(corrected_mA[peak_index])

    tail_mask = (
        (relative_s >= peak_relative_s)
        & (relative_s <= fit_end_s)
        & np.isfinite(corrected_mA)
    )
    tail_t = relative_s[tail_mask] - peak_relative_s
    tail_y = corrected_mA[tail_mask]
    if len(tail_t) < 20:
        raise RuntimeError("not enough tail samples for local current fit")

    bins = np.floor(tail_t / fit_bin_s).astype(int)
    binned = (
        pd.DataFrame({"bin": bins, "t": tail_t, "y": tail_y})
        .groupby("bin", sort=True)
        .agg(t=("t", "median"), y=("y", "median"))
    )
    fit_t = binned["t"].to_numpy(float)
    fit_y = binned["y"].to_numpy(float)

    peak_mA = max(peak_mA, float(np.nanmax(fit_y)))
    amplitude_bound_mA = max(2.5 * peak_mA, 0.02)
    offset_bound_mA = max(0.2 * peak_mA, 0.002)

    def residual(params: np.ndarray) -> np.ndarray:
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
            - fit_y
        )

    result = least_squares(
        residual,
        x0=np.array([peak_mA, 0.7, 0.006, 0.08, 0.0]),
        bounds=(
            np.array([0.0, 0.0, 0.001, 0.02, -offset_bound_mA]),
            np.array([amplitude_bound_mA, 1.0, 0.03, 0.8, offset_bound_mA]),
        ),
        loss="soft_l1",
        f_scale=0.001,
        max_nfev=20000,
    )
    amplitude_total, fast_fraction, tau_fast, tau_slow, offset = result.x
    fit_relative = np.linspace(peak_relative_s, fit_end_s, 800)
    fit_current = dual_exp_signed(
        fit_relative - peak_relative_s,
        float(amplitude_total),
        float(fast_fraction),
        float(tau_fast),
        float(tau_slow),
        float(offset),
    )
    return {
        "peak_relative_s": peak_relative_s,
        "tau_fast_s": float(tau_fast),
        "tau_slow_s": float(tau_slow),
        "fit_relative_s": fit_relative,
        "fit_current_mA": fit_current,
    }


def dmm_equivalent_samples(
    relative_s: np.ndarray,
    current_mA: np.ndarray,
    demo_edge_time_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a high-rate Moku edge using actual 10 Hz DMM timing/read widths."""
    dmm_path = SESS / DMM_REFERENCE_SESSION / "readings.csv"
    dmm = pd.read_csv(dmm_path, usecols=["time", "read_duration_ms"])
    dmm_edge = demo_edge_time_s
    mask = (dmm["time"] >= dmm_edge - 0.05) & (dmm["time"] <= dmm_edge + 0.7)
    dmm_window = dmm.loc[mask].copy()
    sample_relative_s = dmm_window["time"].to_numpy(float) - dmm_edge
    read_width_s = dmm_window["read_duration_ms"].to_numpy(float) / 1000.0

    sampled_mA: list[float] = []
    for sample_t, width_s in zip(sample_relative_s, read_width_s):
        avg_mask = (
            (relative_s >= sample_t - 0.5 * width_s)
            & (relative_s <= sample_t + 0.5 * width_s)
        )
        if np.count_nonzero(avg_mask) >= 2:
            sampled_mA.append(float(np.mean(current_mA[avg_mask])))
        else:
            sampled_mA.append(float(np.interp(sample_t, relative_s, current_mA)))

    return sample_relative_s, np.asarray(sampled_mA), read_width_s


def plot_peak_resolution_demo(out_path: Path) -> None:
    session_dir = SESS / PEAK_DEMO_SESSION
    fits = pd.read_csv(session_dir / "moku_waveform_charge_transfer_edge_fits.csv")
    fit = fits.loc[fits["event_index"] == PEAK_DEMO_EVENT_INDEX].iloc[0]
    edge_time_s = float(fit["edge_time_s"])

    waveform = pd.read_csv(
        session_dir / "moku_waveform.csv",
        usecols=["time", "ch2_voltage"],
    )
    time_s = waveform["time"].to_numpy(float)
    raw_current_mA = waveform["ch2_voltage"].to_numpy(float) / SHUNT_OHMS * 1000.0

    relative_s = time_s - edge_time_s
    peak_relative_s = float(fit["peak_relative_s"])
    x_min, x_max = -0.08, 0.22
    window_mask = (
        (relative_s >= peak_relative_s + x_min)
        & (relative_s <= peak_relative_s + x_max)
    )
    rel = relative_s[window_mask] - peak_relative_s
    corrected_mA = raw_current_mA[window_mask] - float(fit["baseline_mA"])

    fit_relative = np.linspace(peak_relative_s, peak_relative_s + x_max, 800)
    fit_t = fit_relative - peak_relative_s
    fit_x = fit_relative - peak_relative_s
    fit_current = dual_exp_signed(
        fit_t,
        float(fit["amplitude_total_mA"]),
        float(fit["fast_fraction"]),
        float(fit["tau_fast_s"]),
        float(fit["tau_slow_s"]),
        float(fit["offset_signed_mA"]),
    )

    dmm_t, dmm_i, dmm_width = dmm_equivalent_samples(
        relative_s[window_mask],
        corrected_mA,
        edge_time_s,
    )
    dmm_x = dmm_t - peak_relative_s
    dmm_mask = (dmm_x >= x_min) & (dmm_x <= x_max)

    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    ax.scatter(
        rel,
        corrected_mA,
        s=4,
        color="#9ca3af",
        alpha=0.75,
        linewidth=0,
        label="Moku raw points, 10 kHz",
        zorder=2,
    )
    ax.plot(
        fit_x,
        fit_current,
        color="#111827",
        linewidth=2.0,
        label="dual-exponential tail fit",
        zorder=4,
    )
    ax.errorbar(
        dmm_x[dmm_mask],
        dmm_i[dmm_mask],
        xerr=0.5 * dmm_width[dmm_mask],
        fmt="s",
        markersize=5.5,
        color="#d62728",
        ecolor="#d62728",
        elinewidth=1.2,
        capsize=2.0,
        label="same trace sampled like DMM, 10 Hz / 23 ms read",
        zorder=5,
    )
    ax.axvline(0.0, color="#f59e0b", linestyle="--", linewidth=1.2)
    ax.set_title(
        "One relay-edge current peak: high-rate Moku vs DMM-rate sampling\n"
        f"0.6 V close edge at {edge_time_s:.0f} s; peak +{peak_relative_s * 1000:.0f} ms; "
        f"tau_fast={float(fit['tau_fast_s']) * 1000:.1f} ms, "
        f"tau_slow={float(fit['tau_slow_s']) * 1000:.0f} ms",
        pad=12,
    )
    ax.set_xlabel("Time relative to fitted current peak (s)")
    ax.set_ylabel("Baseline-corrected current (mA)")
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.012, 0.16)
    ax.grid(True, alpha=0.25)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.24),
        ncol=3,
        fontsize=8.5,
        frameon=True,
    )
    fig.subplots_adjust(left=0.1, right=0.98, bottom=0.28, top=0.82)
    fig.savefig(out_path.with_suffix(".png"), dpi=180, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def plot_tek_peak_resolution_demo(out_path: Path) -> None:
    session_dir = SESS / TEK_PEAK_DEMO_SESSION
    edge_time_s = TEK_PEAK_DEMO_EDGE_TIME_S
    waveform = pd.read_csv(
        session_dir / "oscilloscope_waveform.csv",
        usecols=["time", "ch2_voltage"],
    )
    time_s = waveform["time"].to_numpy(float)
    raw_current_mA = waveform["ch2_voltage"].to_numpy(float) / SHUNT_OHMS * 1000.0
    relative_s_all = time_s - edge_time_s

    baseline_mask = (
        (relative_s_all >= -0.2)
        & (relative_s_all <= -0.02)
        & np.isfinite(raw_current_mA)
    )
    baseline_mA = float(np.median(raw_current_mA[baseline_mask]))
    corrected_all_mA = raw_current_mA - baseline_mA

    fit_window_mask = (relative_s_all >= -0.02) & (relative_s_all <= 0.45)
    fit = fit_local_dual_exp(
        relative_s_all[fit_window_mask],
        corrected_all_mA[fit_window_mask],
        fit_end_s=0.45,
        peak_search_end_s=0.2,
    )
    peak_relative_s = float(fit["peak_relative_s"])
    x_min, x_max = -0.08, 0.22
    window_mask = (
        (relative_s_all >= peak_relative_s + x_min)
        & (relative_s_all <= peak_relative_s + x_max)
    )
    rel = relative_s_all[window_mask] - peak_relative_s
    corrected_mA = corrected_all_mA[window_mask]
    fit_x = fit["fit_relative_s"] - peak_relative_s
    fit_mask = (fit_x >= x_min) & (fit_x <= x_max)

    dmm_t, dmm_i, dmm_width = dmm_equivalent_samples(
        relative_s_all[window_mask],
        corrected_mA,
        edge_time_s,
    )
    dmm_x = dmm_t - peak_relative_s
    dmm_mask = (dmm_x >= x_min) & (dmm_x <= x_max)

    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    ax.scatter(
        rel,
        corrected_mA,
        s=5,
        color="#9ca3af",
        alpha=0.75,
        linewidth=0,
        label="Tek raw points, 3.1 kHz",
        zorder=2,
    )
    ax.plot(
        fit_x[fit_mask],
        fit["fit_current_mA"][fit_mask],
        color="#111827",
        linewidth=2.0,
        label="local dual-exponential tail fit",
        zorder=4,
    )
    ax.errorbar(
        dmm_x[dmm_mask],
        dmm_i[dmm_mask],
        xerr=0.5 * dmm_width[dmm_mask],
        fmt="s",
        markersize=5.5,
        color="#d62728",
        ecolor="#d62728",
        elinewidth=1.2,
        capsize=2.0,
        label="same trace sampled like DMM, 10 Hz / 23 ms read",
        zorder=5,
    )
    ax.axvline(0.0, color="#f59e0b", linestyle="--", linewidth=1.2)
    ax.set_title(
        "One relay-edge current peak: Tek oscilloscope vs DMM-rate sampling\n"
        f"0.6 V close edge at {edge_time_s:.0f} s; peak +{peak_relative_s * 1000:.1f} ms; "
        f"tau_fast={fit['tau_fast_s'] * 1000:.1f} ms, "
        f"tau_slow={fit['tau_slow_s'] * 1000:.0f} ms",
        pad=12,
    )
    ax.set_xlabel("Time relative to fitted current peak (s)")
    ax.set_ylabel("Baseline-corrected current (mA)")
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.006, max(0.05, 1.12 * float(np.nanmax(corrected_mA))))
    ax.grid(True, alpha=0.25)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.24),
        ncol=3,
        fontsize=8.5,
        frameon=True,
    )
    fig.subplots_adjust(left=0.1, right=0.98, bottom=0.28, top=0.82)
    fig.savefig(out_path.with_suffix(".png"), dpi=180, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def write_notes(data: pd.DataFrame, fits: pd.DataFrame, path: Path) -> None:
    lines = [
        "# Instrument Charge-Fit Comparison",
        "",
        "This figure compares DMM, Tek oscilloscope, and Moku:Pro runs using the",
        "same 750 s `step_voltage_relay2_750s` relay/voltage schedule.",
        "",
        "Method:",
        "",
        "- Current is computed from the shunt voltage with `I = V_shunt / 330 ohm`.",
        "- For each CH2 close edge, current is baseline-corrected and integrated",
        "  over the first 1.0 s after the scheduled edge.",
        "- DMM uses a longer pre-edge baseline window, `-5` to `-1 s`, because it",
        "  only samples at 10 Hz.",
        "- Tek and Moku use `-0.2` to `-0.02 s` pre-edge baseline windows.",
        "- The linear fit is `Q_1s = slope * V + intercept`.",
        "",
        "Sessions:",
        "",
    ]
    for spec in SESSIONS:
        lines.append(f"- {spec.label}: `{spec.session}`")
    lines.extend(["", "Fit summary:", ""])
    lines.extend(
        [
            "| Instrument | Slope (uC/V) | Intercept (uC) | R2 | n |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in fits.itertuples(index=False):
        lines.append(
            f"| {row.instrument} | {row.slope_uC_per_V:.4g} | "
            f"{row.intercept_uC:.4g} | {row.r2:.4g} | {row.n} |"
        )
    lines.extend(
        [
            "",
            "Output files:",
            "",
            "- `instrument_charge_fit_comparison.png`",
            "- `instrument_charge_fit_comparison.svg`",
            "- `instrument_charge_fit_peak_resolution.png`",
            "- `instrument_charge_fit_peak_resolution.svg`",
            "- `instrument_charge_fit_peak_resolution_tek.png`",
            "- `instrument_charge_fit_peak_resolution_tek.svg`",
            "- `instrument_charge_fit_comparison_points.csv`",
            "- `instrument_charge_fit_comparison_fits.csv`",
            "",
            "Peak-resolution example:",
            "",
            "![One 0.6 V current peak with DMM-rate sampling](instrument_charge_fit_peak_resolution.svg)",
            "",
            "- The peak-resolution figure uses the Moku:Pro 0.6 V CH2-close edge",
            f"  `{PEAK_DEMO_SESSION}`, event `{PEAK_DEMO_EVENT_INDEX}` at 450 s.",
            "- Grey points are all high-rate Moku samples in the zoomed window.",
            "- Red squares are not a separate DMM run; they are the same Moku trace",
            "  sampled at the actual 10 Hz DMM timestamps with the observed ~23 ms",
            "  read-duration boxcar average. This isolates the sampling limitation.",
            "- The fitted fast time constant is only a few milliseconds, much shorter",
            "  than both the DMM sample spacing and read duration, so a DMM can still",
            "  give a reasonable 1 s integral while missing the true peak amplitude",
            "  and early decay shape.",
            "",
            "Oscilloscope peak-resolution example:",
            "",
            "![One 0.6 V Tek current peak with DMM-rate sampling](instrument_charge_fit_peak_resolution_tek.svg)",
            "",
            "- This second peak-resolution figure uses the Tek oscilloscope 0.6 V",
            f"  CH2-close edge `{TEK_PEAK_DEMO_SESSION}` at 250 s.",
            "- It applies the same DMM-rate sampling overlay to the Tek trace, using",
            "  the actual DMM 10 Hz timestamps and observed read durations.",
            "- The Tek trace has lower sampling rate than Moku but still resolves the",
            "  current peak and decay far better than the DMM stream.",
            "",
            "Interpretation caution:",
            "",
            "This is a cross-instrument comparison of early charge transfer, not the",
            "full stored charge over the complete 50 s relay-closed window. Long raw",
            "integrals of high-rate shunt data are sensitive to tiny DC offsets; the",
            "1 s window is used to keep the comparison stable across instruments.",
        ]
    )
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = pd.concat([load_rows(spec) for spec in SESSIONS], ignore_index=True)
    fits = pd.DataFrame([fit_rows(data[data["instrument"] == spec.label]) for spec in SESSIONS])

    points_path = OUT / "instrument_charge_fit_comparison_points.csv"
    fits_path = OUT / "instrument_charge_fit_comparison_fits.csv"
    data.to_csv(points_path, index=False)
    fits.to_csv(fits_path, index=False)
    plot(data, fits, OUT / "instrument_charge_fit_comparison")
    plot_peak_resolution_demo(OUT / "instrument_charge_fit_peak_resolution")
    plot_tek_peak_resolution_demo(OUT / "instrument_charge_fit_peak_resolution_tek")
    write_notes(data, fits, OUT / "instrument_charge_fit_comparison.md")

    print(data.to_string(index=False))
    print()
    print(fits.to_string(index=False))
    print(f"\nWrote outputs to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
