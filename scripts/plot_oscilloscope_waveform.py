#!/usr/bin/env python3
"""Plot oscilloscope voltage/current CSV data to an SVG."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from matplotlib.ticker import MaxNLocator
except ImportError as exc:
    print(
        "error: missing plotting dependency. Run with `uv run python3 "
        "scripts/plot_oscilloscope_waveform.py ...` or install project dependencies.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


DEFAULT_SHUNT_OHMS = 330.0
WAVEFORM_COLUMNS = ("time", "scope_time", "ch1_voltage", "ch2_voltage")
READINGS_COLUMNS = ("time", "dmm1_voltage", "dmm2_voltage")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot oscilloscope waveform/readings CSV data as an SVG with "
            "CH1 voltage and CH2-derived current subplots."
        )
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to oscilloscope_waveform.csv or readings.csv",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output SVG path. Defaults to oscilloscope_waveform.svg beside the CSV.",
    )
    parser.add_argument(
        "--x-column",
        choices=("time", "scope_time"),
        default="time",
        help="Column to use for the shared x axis. scope_time is only available for waveform CSVs.",
    )
    parser.add_argument(
        "--x-min",
        type=float,
        help="Minimum x-axis value to plot, in the selected --x-column units.",
    )
    parser.add_argument(
        "--x-max",
        type=float,
        help="Maximum x-axis value to plot, in the selected --x-column units.",
    )
    parser.add_argument(
        "--shunt-ohms",
        type=float,
        default=DEFAULT_SHUNT_OHMS,
        help="Shunt resistance used to convert CH2 voltage to current. Defaults to 330.",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=30000,
        help=(
            "Maximum points per plotted trace after peak-preserving downsampling. "
            "Use 0 to plot every row. Defaults to 30000."
        ),
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        help=(
            "Optional second SVG path for a less-noisy binned median analysis plot."
        ),
    )
    parser.add_argument(
        "--analysis-bin-ms",
        type=float,
        default=10.0,
        help=(
            "Time-bin width for --analysis-output in milliseconds. Defaults to 10."
        ),
    )
    parser.add_argument(
        "--tracking-csv",
        type=Path,
        help=(
            "Explicit tip-tracking CSV (manual 'time,dx,dy' or OpenCV format) for the "
            "deflection panel of --analysis-output. Default: auto-detect in the session "
            "folder, preferring manual Blender tracking over the OpenCV export."
        ),
    )
    parser.add_argument(
        "--no-deflection",
        action="store_true",
        help="Skip the angular-deflection panel even if tracking data is available.",
    )
    return parser.parse_args()


def validate_input(csv_path: Path, shunt_ohms: float) -> None:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file does not exist: {csv_path}")
    if not csv_path.is_file():
        raise RuntimeError(f"CSV path is not a file: {csv_path}")
    if shunt_ohms <= 0:
        raise RuntimeError("--shunt-ohms must be greater than 0")


def read_waveform(csv_path: Path) -> tuple[pd.DataFrame, str, str]:
    data = pd.read_csv(csv_path)

    if all(column in data.columns for column in WAVEFORM_COLUMNS):
        required_columns = WAVEFORM_COLUMNS
        voltage_column = "ch1_voltage"
        current_voltage_column = "ch2_voltage"
    elif all(column in data.columns for column in READINGS_COLUMNS):
        required_columns = READINGS_COLUMNS
        voltage_column = "dmm1_voltage"
        current_voltage_column = "dmm2_voltage"
    else:
        accepted = " / ".join(
            [", ".join(WAVEFORM_COLUMNS), ", ".join(READINGS_COLUMNS)]
        )
        raise RuntimeError(f"CSV does not match an accepted column set: {accepted}")

    for column in required_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if "current_mA" in data.columns:
        data["current_mA"] = pd.to_numeric(data["current_mA"], errors="coerce")

    data = data.dropna(subset=list(required_columns)).sort_values("time")
    if data.empty:
        raise RuntimeError("CSV contains no plottable waveform rows")

    return data, voltage_column, current_voltage_column


def default_output_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".svg")


def filter_x_range(
    data: pd.DataFrame,
    x_column: str,
    x_min: float | None,
    x_max: float | None,
) -> pd.DataFrame:
    if x_column not in data.columns:
        raise RuntimeError(f"CSV does not contain x-axis column: {x_column}")
    if x_min is not None and x_max is not None and x_min >= x_max:
        raise RuntimeError("--x-min must be less than --x-max")

    filtered = data
    if x_min is not None:
        filtered = filtered[filtered[x_column] >= x_min]
    if x_max is not None:
        filtered = filtered[filtered[x_column] <= x_max]
    if filtered.empty:
        raise RuntimeError("Selected x-axis range contains no plottable waveform rows")
    return filtered


def peak_preserving_downsample(
    x_values: pd.Series,
    y_values: pd.Series,
    max_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce plotted points while retaining each chunk's min/max excursions."""
    x = x_values.to_numpy()
    y = y_values.to_numpy()
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]

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

    if not selected_indices:
        return x, y

    selected = np.array(selected_indices, dtype=int)
    return x[selected], y[selected]


def cumulative_charge_mc(time_seconds: np.ndarray, current_ma: np.ndarray) -> np.ndarray:
    """Cumulative charge in mC from current in mA integrated over time in s."""
    t = np.asarray(time_seconds, dtype=float)
    i = np.asarray(current_ma, dtype=float)
    charge = np.zeros_like(i)
    if len(i) > 1:
        charge[1:] = np.cumsum(0.5 * (i[1:] + i[:-1]) * np.diff(t))
    return charge


def _read_tracking_csv(path: Path) -> pd.DataFrame:
    """Load a tracking CSV in either format into columns [time, dx, dy].

    Manual Blender exports are headerless ``time, dx, dy``. OpenCV exports have a
    header with ``time_s``, ``displacement_x_px``, ``displacement_y_px``. The
    first cell tells them apart: numeric -> manual, text header -> OpenCV.
    """
    first_cell = str(pd.read_csv(path, nrows=1, header=None).iloc[0, 0]).strip()
    is_headerless = re.fullmatch(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", first_cell) is not None
    if is_headerless:
        df = pd.read_csv(path, header=None, names=["time", "dx", "dy"], skipinitialspace=True)
    else:
        df = pd.read_csv(path).rename(
            columns={
                "time_s": "time",
                "displacement_x_px": "dx",
                "displacement_y_px": "dy",
            }
        )
        missing = {"time", "dx", "dy"} - set(df.columns)
        if missing:
            raise RuntimeError(f"tracking CSV {path} missing columns: {sorted(missing)}")
    df = df[["time", "dx", "dy"]].apply(pd.to_numeric, errors="coerce").dropna()
    return df.sort_values("time")


def load_deflection(
    csv_path: Path, override: Path | None = None
) -> tuple[np.ndarray, np.ndarray, str] | None:
    """Return (time, theta_rad, source_label) for the session, or None.

    theta = atan(dy/dx). Prefers manual Blender tracking
    (``motion-tracking/user-data/data/<id>.csv``) over the OpenCV session export
    (``<session>/MVI_<id>_opencv.csv``). atan(dy/dx) is invariant to the
    manual-vs-OpenCV displacement sign flip, so both conventions give the same
    angle.
    """
    if override is not None:
        source, label = override, f"override: {override.name}"
    else:
        session_dir = csv_path.parent
        videos = (
            sorted(session_dir.glob("MVI_*.mp4"))
            or sorted(session_dir.glob("MVI_*.MOV"))
            or sorted(session_dir.glob("MVI_*.mov"))
        )
        video_num = videos[0].stem.replace("MVI_", "") if videos else None
        repo_root = Path(__file__).resolve().parents[1]
        manual = (
            repo_root / "motion-tracking" / "user-data" / "data" / f"{video_num}.csv"
            if video_num
            else None
        )
        opencv = sorted(session_dir.glob("MVI_*_opencv.csv")) or sorted(
            session_dir.glob("*_opencv.csv")
        )
        if manual is not None and manual.exists():
            source, label = manual, f"manual: {manual.name}"
        elif opencv:
            source, label = opencv[0], f"opencv: {opencv[0].name}"
        else:
            return None

    df = _read_tracking_csv(source)
    if df.empty:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        theta = np.arctan(df["dy"].to_numpy() / df["dx"].to_numpy())
    time = df["time"].to_numpy()
    good = np.isfinite(time) & np.isfinite(theta)
    if not good.any():
        return None
    return time[good], theta[good], label


def plot_waveform(
    data: pd.DataFrame,
    output_path: Path,
    x_column: str,
    voltage_column: str,
    current_voltage_column: str,
    shunt_ohms: float,
    max_points: int,
) -> None:
    if x_column not in data.columns:
        raise RuntimeError(f"CSV does not contain x-axis column: {x_column}")

    current_ma = (
        data["current_mA"]
        if "current_mA" in data.columns and data["current_mA"].notna().any()
        else data[current_voltage_column] / shunt_ohms * 1000.0
    )
    voltage_x, voltage_y = peak_preserving_downsample(
        data[x_column],
        data[voltage_column],
        max_points,
    )
    current_x, current_y = peak_preserving_downsample(
        data[x_column],
        current_ma,
        max_points,
    )

    if x_column == "time":
        x_label = "Time since measurement t0 (s)"
    else:
        x_label = "Oscilloscope time (s)"

    fig, (voltage_ax, current_ax) = plt.subplots(
        nrows=2,
        ncols=1,
        sharex=True,
        figsize=(11, 7),
        constrained_layout=True,
    )

    voltage_ax.plot(voltage_x, voltage_y, color="#1f77b4", linewidth=1.1)
    voltage_ax.set_ylabel("CH1 voltage (V)")
    voltage_ax.grid(True, color="#d9d9d9", linewidth=0.7, alpha=0.8)

    current_ax.plot(current_x, current_y, color="#d62728", linewidth=1.1)
    current_ax.set_ylabel("Current (mA)")
    current_ax.set_xlabel(x_label)
    current_ax.grid(True, color="#d9d9d9", linewidth=0.7, alpha=0.8)

    fig.suptitle("Oscilloscope Waveform")
    fig.savefig(output_path, format="svg")
    plt.close(fig)


def plot_analysis_waveform(
    data: pd.DataFrame,
    output_path: Path,
    x_column: str,
    voltage_column: str,
    current_voltage_column: str,
    shunt_ohms: float,
    bin_ms: float,
    deflection: "tuple[np.ndarray, np.ndarray, str] | None" = None,
) -> None:
    """Binned-median voltage, current, charge, and (if tracked) deflection.

    Charge is the cumulative integral of current over time. Deflection is
    atan(dy/dx) from manual or OpenCV tip tracking. All panels share the time
    axis with no vertical gap between them.
    """
    if x_column not in data.columns:
        raise RuntimeError(f"CSV does not contain x-axis column: {x_column}")
    if bin_ms <= 0:
        raise RuntimeError("--analysis-bin-ms must be greater than 0")

    current_source_column = (
        "current_mA"
        if "current_mA" in data.columns and data["current_mA"].notna().any()
        else current_voltage_column
    )
    analysis_data = data[[x_column, voltage_column, current_source_column]].copy()
    if current_source_column == "current_mA":
        analysis_data["current_ma"] = analysis_data["current_mA"]
    else:
        analysis_data["current_ma"] = (
            analysis_data[current_voltage_column] / shunt_ohms * 1000.0
        )
    analysis_data["charge_mc"] = cumulative_charge_mc(
        analysis_data[x_column].to_numpy(dtype=float),
        analysis_data["current_ma"].to_numpy(dtype=float),
    )

    bin_width_seconds = bin_ms / 1000.0

    def bin_summary(x_values: np.ndarray, y_values: np.ndarray) -> pd.DataFrame:
        x_arr = np.asarray(x_values, dtype=float)
        y_arr = np.asarray(y_values, dtype=float)
        bins = ((x_arr - float(np.nanmin(x_arr))) / bin_width_seconds).astype(np.int64)
        frame = pd.DataFrame({"_bin": bins, "x": x_arr, "y": y_arr})
        return (
            frame.groupby("_bin", sort=True)
            .agg(
                x=("x", "median"),
                med=("y", "median"),
                p05=("y", lambda v: v.quantile(0.05)),
                p95=("y", lambda v: v.quantile(0.95)),
            )
            .reset_index(drop=True)
        )

    panels = [
        (bin_summary(analysis_data[x_column], analysis_data[voltage_column]), "#1f77b4", "CH1 voltage (V)"),
        (bin_summary(analysis_data[x_column], analysis_data["current_ma"]), "#d62728", "Current (mA)"),
        (bin_summary(analysis_data[x_column], analysis_data["charge_mc"]), "#2ca02c", "Charge (mC)"),
    ]
    if panels[0][0].empty:
        raise RuntimeError("No analysis bins produced")

    if deflection is not None:
        td, theta, _ = deflection
        x_lo = float(analysis_data[x_column].min())
        x_hi = float(analysis_data[x_column].max())
        in_range = (td >= x_lo) & (td <= x_hi)
        if in_range.any():
            panels.append(
                (bin_summary(td[in_range], theta[in_range]), "#9467bd", "Deflection atan(dy/dx) (rad)")
            )

    x_label = "Time since measurement t0 (s)" if x_column == "time" else "Oscilloscope time (s)"

    fig, axes = plt.subplots(
        nrows=len(panels),
        ncols=1,
        sharex=True,
        figsize=(11, 2.3 * len(panels) + 0.6),
        gridspec_kw={"hspace": 0.0},
    )
    axes = np.atleast_1d(axes)

    for ax, (summary, color, ylabel) in zip(axes, panels):
        sx = summary["x"].to_numpy(dtype=float)
        ax.fill_between(
            sx,
            summary["p05"].to_numpy(dtype=float),
            summary["p95"].to_numpy(dtype=float),
            color=color,
            alpha=0.18,
            linewidth=0,
        )
        ax.plot(sx, summary["med"].to_numpy(dtype=float), color=color, linewidth=1.3)
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#d9d9d9", linewidth=0.7, alpha=0.8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))

    axes[-1].set_xlabel(x_label)
    fig.suptitle(f"Oscilloscope Waveform Analysis ({bin_ms:g} ms median bins)")
    fig.align_ylabels(axes)
    fig.savefig(output_path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _frontend_range_half_volts(range_str: object) -> float | None:
    """Half of a Moku frontend range like '400mVpp' -> 0.2 V; None if unparseable."""
    match = re.match(
        r"\s*([-+]?\d+(?:\.\d+)?)\s*([munp]?)Vpp\s*$", str(range_str), re.IGNORECASE
    )
    if not match:
        return None
    scale = {"": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}
    return float(match.group(1)) * scale[match.group(2).lower()] / 2.0


def report_clipping(csv_path: Path, data: pd.DataFrame, tolerance: float = 0.01) -> None:
    """Warn on stderr if any Moku input channel reached its frontend-range rail.

    Uses the sibling <stem>_metadata.json (frontend ranges + probe attenuations).
    Does nothing for CSVs without that metadata, e.g. oscilloscope/DMM records.
    """
    metadata_path = csv_path.with_name(f"{csv_path.stem}_metadata.json")
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, ValueError):
        return
    ranges = metadata.get("frontend_ranges") or {}
    attenuations = metadata.get("probe_attenuation") or {}
    if not ranges:
        return

    any_clipped = False
    for channel in ("ch1", "ch2", "ch3"):
        column = f"{channel}_voltage"
        rail_volts = _frontend_range_half_volts(ranges.get(channel))
        if column not in data.columns or rail_volts is None:
            continue
        attenuation = float(attenuations.get(channel, 1.0)) or 1.0
        series = pd.to_numeric(data[column], errors="coerce").dropna()
        if series.empty:
            continue
        input_referred = series.abs() / attenuation
        clipped = int((input_referred >= rail_volts * (1.0 - tolerance)).sum())
        if clipped:
            any_clipped = True
            percent = 100.0 * clipped / len(series)
            print(
                f"WARNING: {channel} clipping - {clipped} samples ({percent:.2f}%) at/over the "
                f"{ranges.get(channel)} rail (+/-{rail_volts:g} V at the Moku input, "
                f"+/-{rail_volts * attenuation:g} V in CSV units). "
                "Reduce amplifier gain or drive amplitude.",
                file=sys.stderr,
            )
    if not any_clipped:
        print("Clipping check: no channel reached the Moku input rail.", file=sys.stderr)


def main() -> int:
    args = parse_args()
    output_path = args.output or default_output_path(args.csv_path)

    try:
        validate_input(args.csv_path, args.shunt_ohms)
        data, voltage_column, current_voltage_column = read_waveform(args.csv_path)
        report_clipping(args.csv_path, data)
        data = filter_x_range(data, args.x_column, args.x_min, args.x_max)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plot_waveform(
            data=data,
            output_path=output_path,
            x_column=args.x_column,
            voltage_column=voltage_column,
            current_voltage_column=current_voltage_column,
            shunt_ohms=args.shunt_ohms,
            max_points=args.max_points,
        )
        deflection = None
        if args.analysis_output:
            if not args.no_deflection:
                deflection = load_deflection(args.csv_path, args.tracking_csv)
            args.analysis_output.parent.mkdir(parents=True, exist_ok=True)
            plot_analysis_waveform(
                data=data,
                output_path=args.analysis_output,
                x_column=args.x_column,
                voltage_column=voltage_column,
                current_voltage_column=current_voltage_column,
                shunt_ohms=args.shunt_ohms,
                bin_ms=args.analysis_bin_ms,
                deflection=deflection,
            )
    except (FileNotFoundError, RuntimeError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {output_path}")
    if args.analysis_output:
        print(f"Wrote {args.analysis_output}")
        if deflection is not None:
            print(f"Deflection panel from {deflection[2]}")
        elif not args.no_deflection:
            print("Deflection panel: no manual or OpenCV tracking found for this session")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
