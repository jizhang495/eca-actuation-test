#!/usr/bin/env python3
"""Generate the figures for the ECA characterization report.

Reproducible extraction of the *within-session / within-day* electromechanical
characterization, plus a compact view of the cross-day drift that is treated as
a confound (not the headline). Reads the session CSVs directly and writes
figures + a tidy data CSV into ``user-data/reports/``.

Outputs (user-data/reports/):
  fig_dc_transfer.{png,svg}    deflection swing & charge vs signed step voltage
  fig_frequency_response.*     deflection (low-pass) & current (capacitive) vs f
  fig_ac_linearity.*           deflection & current vs AC drive amplitude
  fig_drift_confound.*         step response time (t63) & stroke vs date
  characterization_data.csv    the aggregated numbers behind the figures
"""

from __future__ import annotations

import json
import glob
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
SESS = REPO / "user-data" / "sessions"
OUT = REPO / "user-data" / "reports"


# ---------- helpers ----------
def theta_rad(tr: pd.DataFrame) -> np.ndarray:
    """theta = atan(dy/dx); invariant to the manual/opencv sign flip."""
    return np.arctan(
        tr["displacement_y_px"].to_numpy(float) / tr["displacement_x_px"].to_numpy(float)
    )


def half_pp(sig: np.ndarray, mask: np.ndarray) -> float:
    """Robust half peak-to-peak amplitude (97-3 percentile) over a window."""
    s = sig[mask]
    s = s[np.isfinite(s)]
    if len(s) < 10:
        return np.nan
    return (np.percentile(s, 97) - np.percentile(s, 3)) / 2.0


def signed_voltage(test: str) -> float | None:
    m = re.search(r"step_(neg)?(\d)p(\d)v", test)
    if not m:
        return None
    v = float(f"{m.group(2)}.{m.group(3)}")
    return -v if m.group(1) else v


def load_track(session_dir: str):
    mt = sorted(glob.glob(os.path.join(session_dir, "*_opencv.csv")))
    if not mt:
        return None
    tr = pd.read_csv(mt[0])
    if "match_score" not in tr or tr["match_score"].median() < 0.4:
        return None
    tr = tr[tr["match_score"] > 0.4]
    return tr["time_s"].to_numpy(float), theta_rad(tr)


# ---------- 1. DC step transfer curve (within-day: 2026-06-18 series) ----------
def dc_transfer() -> pd.DataFrame:
    rows = []
    for d in sorted(glob.glob(str(SESS / "2026-06-18_*step_*v_moku") + "/")):
        cfg = json.loads(open(os.path.join(d, "config.json")).read())
        V = signed_voltage(cfg["test_name"])
        if V is None:
            continue
        relays = cfg["relay_ch2_stages"]
        tk = load_track(d)
        if tk is None:
            continue
        tt, th = tk
        swings = []
        for r in relays:
            tc, to = r["start_time"], r["end_time"]
            rest = th[(tt >= tc - 8) & (tt < tc - 1)]
            chg = th[(tt >= to - 8) & (tt < to - 1)]
            if len(rest) > 3 and len(chg) > 3:
                swings.append(np.median(chg) - np.median(rest))
        if swings:
            rows.append(dict(V=V, swing_mrad=1000 * np.median(swings)))
    R = pd.DataFrame(rows)
    return R.groupby("V", as_index=False).agg(
        swing_mrad=("swing_mrad", "mean"), n=("V", "size")
    )


# ---------- 2/3. frequency & amplitude sweeps (within-session: 2026-06-09) ----------
def sweep(session_dir: str) -> pd.DataFrame:
    cfg = json.loads(open(os.path.join(session_dir, "config.json")).read())
    gens = cfg["moku_waveform_generator_stages"]
    w = pd.read_csv(
        os.path.join(session_dir, "moku_waveform.csv"),
        usecols=["time", "ch1_voltage", "current_mA"],
    )
    t = w["time"].to_numpy(float)
    v = w["ch1_voltage"].to_numpy(float)
    i = pd.to_numeric(w["current_mA"], errors="coerce").to_numpy()
    tk = load_track(session_dir)
    out = []
    for g in gens:
        a, b = g["start_time"], g["end_time"]
        mid = a + (b - a) * 0.5
        me = (t >= mid) & (t < b)
        Da = np.nan
        if tk is not None:
            tt, th = tk
            Da = 1000 * half_pp(th, (tt >= mid) & (tt < b))
        out.append(
            dict(
                f=g.get("frequency_hz"),
                vpp=g.get("vpp"),
                Vamp=half_pp(v, me),
                Iamp_uA=1000 * half_pp(i, me),
                defl_mrad=Da,
            )
        )
    return pd.DataFrame(out)


# ---------- 4. drift confound: 0.6 V step response time across the campaign ----------
def _t63(session_dir: str, tc: float, to: float):
    """(swing_mrad, t63_s) for a 0.6 V charge starting at tc, plateau by to."""
    tk = load_track(session_dir)
    if tk is None:
        return None
    tt, th = tk
    rest = th[(tt >= tc - 8) & (tt < tc - 1)]
    plat = th[(tt >= to - 6) & (tt < to - 1)]
    if len(rest) < 5 or len(plat) < 5:
        return None
    th0 = np.median(rest)
    swing = np.median(plat) - th0
    if abs(swing) < 0.005:
        return None
    win = (tt >= tc) & (tt < to)
    twin, thwin = tt[win], th[win] - th0
    idx = np.where((thwin / swing) >= 0.63)[0]
    t63 = twin[idx[0]] - tc if len(idx) else np.nan
    return swing * 1000.0, t63


def drift() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two like-for-like 0.6 V response-time series at fixed device-stroke.

    mid-train  : relay2 staircase 0.6 V plateau (after 0.2/0.4 V cycling) ->
                 06-05..06-18; internally consistent gradual trend.
    from-rest  : first 0.6 V charge from full rest -> step_0p6v 1st pulse
                 (06-18) and constV_0p6v hold (06-19). The 06-18 overlap with the
                 mid-train series calibrates the protocol offset, so the 06-19
                 end-stage jump is genuine degradation, not protocol.
    """
    mid, rest = [], []
    for d in sorted(glob.glob(str(SESS / "*step_voltage_relay2_750s_moku") + "/")):
        r = _t63(d, 250.0, 300.0)  # 0.6 V plateau mid-train
        if r:
            mid.append(dict(date=os.path.basename(d.rstrip("/"))[:10],
                            swing_mrad=r[0], t63_s=r[1]))
    for d in sorted(glob.glob(str(SESS / "2026-06-18_*step_0p6v_moku") + "/")):
        r = _t63(d, 50.0, 100.0)  # first relay pulse, from rest
        if r:
            rest.append(dict(date="2026-06-18", swing_mrad=r[0], t63_s=r[1]))
    for d in sorted(glob.glob(str(SESS / "2026-06-19_*hold_constV_0p6v_500s_moku") + "/")):
        r = _t63(d, 30.0, 80.0)  # CH2 close from rest
        if r:
            # split the 06-19 cluster into am/pm to show the within-day progression
            hh = int(os.path.basename(d.rstrip("/"))[11:13])
            rest.append(dict(date="2026-06-19" + ("am" if hh < 6 else "pm"),
                             swing_mrad=r[0], t63_s=r[1]))
    midR = (
        pd.DataFrame(mid)
        .groupby("date", as_index=False)
        .agg(t63_s=("t63_s", "median"),
             abs_swing_mrad=("swing_mrad", lambda s: np.median(np.abs(s))))
    )
    restR = (
        pd.DataFrame(rest)
        .groupby("date", as_index=False)
        .agg(t63_s=("t63_s", "median"),
             abs_swing_mrad=("swing_mrad", lambda s: np.median(np.abs(s))))
    )
    return midR, restR


# ---------- figures ----------
def fig_dc(R: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.axhline(0, color="#888", lw=0.8)
    ax.axvline(0, color="#888", lw=0.8)
    ax.plot(R["V"], R["swing_mrad"], "o-", color="#1f77b4", ms=7)
    # symmetric reference (mirror of the positive branch) to show asymmetry
    pos = R[R["V"] > 0].sort_values("V")
    if len(pos):
        ax.plot(
            -pos["V"], -pos["swing_mrad"], "--", color="#d62728", lw=1.2,
            label="mirror of +V branch",
        )
    ax.set_xlabel("Step voltage (V)")
    ax.set_ylabel("Deflection swing (mrad)")
    ax.set_title("DC step actuation transfer curve (2026-06-18, device state fixed)")
    ax.grid(alpha=0.3)
    ax.legend()
    for _, r in R.iterrows():
        ax.annotate(f"{r['swing_mrad']:.0f}", (r["V"], r["swing_mrad"]),
                    textcoords="offset points", xytext=(4, 5), fontsize=8)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"fig_dc_transfer.{ext}", dpi=110, bbox_inches="tight")
    plt.close(fig)


def fig_freq(sine: pd.DataFrame, square: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    ax2 = ax.twinx()
    for df, lab, c in ((sine, "sine", "#1f77b4"), (square, "square", "#2ca02c")):
        m = df.dropna(subset=["defl_mrad"])
        ax.plot(m["f"], m["defl_mrad"], "o-", color=c, label=f"deflection ({lab})")
    ax2.plot(sine["f"], sine["Iamp_uA"], "s--", color="#d62728", label="current (sine)")
    # -3 dB guide off the sine low-frequency deflection
    d0 = sine["defl_mrad"].iloc[:3].max()
    ax.axhline(d0 / np.sqrt(2), color="#999", ls=":", lw=1)
    ax.axvline(0.3, color="#999", ls=":", lw=1)
    ax.set_xscale("log")
    ax.set_xlabel("Drive frequency (Hz)")
    ax.set_ylabel("Deflection amplitude (mrad)")
    ax2.set_ylabel("Current amplitude (uA)", color="#d62728")
    ax.set_title("Frequency response (±0.5 V, 2026-06-09): mechanical low-pass, capacitive current")
    ax.grid(alpha=0.3, which="both")
    ax.legend(loc="upper right")
    ax2.legend(loc="center right")
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"fig_frequency_response.{ext}", dpi=110, bbox_inches="tight")
    plt.close(fig)


def fig_ac(amp: pd.DataFrame) -> None:
    m = amp.dropna(subset=["defl_mrad", "Vamp"])
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(m["Vamp"], m["defl_mrad"], "o-", color="#1f77b4", label="deflection")
    s = np.linalg.lstsq(
        np.vstack([m["Vamp"], np.ones(len(m))]).T, m["defl_mrad"], rcond=None
    )[0]
    xs = np.linspace(0, m["Vamp"].max(), 50)
    ax.plot(xs, s[0] * xs + s[1], "--", color="#888",
            label=f"{s[0]:.0f} mrad/V")
    ax.set_xlabel("AC drive amplitude (V, half p-p)")
    ax.set_ylabel("Deflection amplitude (mrad)")
    ax.set_title("AC amplitude linearity (sine, 0.1 Hz, 2026-06-09)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"fig_ac_linearity.{ext}", dpi=110, bbox_inches="tight")
    plt.close(fig)


def fig_drift(mid: pd.DataFrame, rest: pd.DataFrame) -> None:
    # common categorical date axis across both series
    order = sorted(set(mid["date"]) | set(rest["date"]))
    xi = {d: i for i, d in enumerate(order)}
    fig, (ax, axb) = plt.subplots(
        2, 1, figsize=(8.5, 7), sharex=True,
        gridspec_kw={"height_ratios": [2, 1], "hspace": 0.08},
    )
    # top: response time t63 (log scale spans 0.4 -> 18 s)
    ax.plot([xi[d] for d in mid["date"]], mid["t63_s"], "o-", color="#d62728",
            label="mid-train 0.6 V step (relay2)")
    ax.plot([xi[d] for d in rest["date"]], rest["t63_s"], "D--", color="#9467bd",
            label="from-rest 0.6 V step (step/constV)")
    ax.set_yscale("log")
    ax.set_ylabel("Response time t63 (s)")
    ax.set_title("0.6 V response time across the campaign (stroke fixed) — the drift confound")
    ax.grid(alpha=0.3, which="both")
    ax.legend(loc="upper left", fontsize=9)
    ax.annotate("06-18 in both series:\nfrom-rest ≈1.7× mid-train\n(protocol offset, calibrated)",
                xy=(xi["2026-06-18"], 1.5), xytext=(xi.get("2026-06-12", 1), 4),
                fontsize=8, ha="left",
                arrowprops=dict(arrowstyle="->", color="#666", lw=0.8))
    # bottom: stroke held until the very end
    ax.figure  # noqa
    axb.plot([xi[d] for d in mid["date"]], mid["abs_swing_mrad"], "o-", color="#1f77b4")
    axb.plot([xi[d] for d in rest["date"]], rest["abs_swing_mrad"], "D--", color="#1f77b4")
    axb.set_ylabel("Stroke |swing| (mrad)")
    axb.set_ylim(0, max(mid["abs_swing_mrad"].max(), rest["abs_swing_mrad"].max()) * 1.2)
    axb.grid(alpha=0.3)
    axb.set_xticks(list(xi.values()))
    axb.set_xticklabels(order, rotation=30, ha="right", fontsize=8)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"fig_drift_confound.{ext}", dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    dc = dc_transfer()
    sine = sweep(str(SESS / "2026-06-09_16-16-55_moku_freqsweep_sine_0p5v"))
    square = sweep(str(SESS / "2026-06-09_17-26-10_moku_freqsweep_square_0p5v"))
    amp = sweep(str(SESS / "2026-06-09_18-16-02_moku_ampsweep_sine_0p1hz"))
    dr_mid, dr_rest = drift()

    fig_dc(dc)
    fig_freq(sine, square)
    fig_ac(amp)
    fig_drift(dr_mid, dr_rest)

    # tidy data dump
    with open(OUT / "characterization_data.csv", "w") as f:
        f.write("# DC step transfer (2026-06-18)\n")
        dc.to_csv(f, index=False)
        f.write("\n# frequency sweep sine 0.5V (2026-06-09)\n")
        sine.to_csv(f, index=False)
        f.write("\n# amplitude sweep sine 0.1Hz (2026-06-09)\n")
        amp.to_csv(f, index=False)
        f.write("\n# drift: mid-train 0.6V step (relay2) across dates\n")
        dr_mid.to_csv(f, index=False)
        f.write("\n# drift: from-rest 0.6V step (step_0p6v 06-18, constV_0p6v 06-19)\n")
        dr_rest.to_csv(f, index=False)

    print("DC transfer:\n", dc.to_string(index=False))
    print("\nDrift mid-train:\n", dr_mid.to_string(index=False))
    print("Drift from-rest:\n", dr_rest.to_string(index=False))
    print(f"\nWrote figures + characterization_data.csv to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
