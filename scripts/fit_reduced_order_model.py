#!/usr/bin/env python3
"""Fit the digital-twin reduced-order model to the campaign data.

Corroborates the Soft Matter (Zhang et al. 2024, D4SM00886C) reduced-order model
— series volumetric capacitors -> strain-to-charge -> multilayer-beam curvature,
plus the electronic-transport timescale t_l = (C_v/sigma_e) l^2 — against this
device's measurements. Extracts the material parameters C_v, alpha, and sigma_e,
tests whether the electrode-thickness asymmetry can explain the measured polarity
asymmetry, and fits the mechanical frequency response.

Anchors on the FRESH device (2026-05-06, runs 6998/6999: DMM current via the
330 ohm shunt + manual tip tracking — the same data analysed in the digital-twin
DMSO findings) so the fitted parameters are pre-degradation.

Outputs to user-data/reports/:
  fig_model_charge_voltage.*     Q(V) with the C_v fit
  fig_model_strain_charge.*      theta(Q) with the alpha fit
  fig_model_frequency_fit.*      deflection roll-off with the first-order fit
  reduced_order_fit_params.csv   fitted vs literature parameters
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
SESS = REPO / "user-data" / "sessions"
OUT = REPO / "user-data" / "reports"
MANUAL = REPO / "motion-tracking" / "user-data" / "data"
sys.path.insert(
    0, str(REPO / "user-data" / "eca-digital-twin" / "simulations" / "reduced-order" / "src")
)
from eca_reduced_order import Layer, multilayer_curvature  # noqa: E402

# --- device geometry (experimental.md) and literature moduli ---
L, W = 3000e-6, 200e-6
AREA = L * W
H_TOP, H_BOT, H_NAF = 1.16e-6, 0.53e-6, 2.9e-6
H_ENC_BOT, H_ENC_TOP = 0.4e-6, 0.5e-6   # passive Nafion encapsulation (experimental.md)
E_P, E_N = 7.0e9, 2.5e9
SHUNT = 330.0

# literature targets (digital-twin README / Zhang et al.)
CV_LIT = 38.0            # F/cm^3
ALPHA_LIT = (0.44e-10, 0.69e-10)  # m^3/C
SIGMA_PRISTINE = 0.5     # S/cm (pristine PEDOT:PSS, Zhang et al.)
SIGMA_MEASURED = 500.0   # S/cm (4-probe, printed DMSO-PEDOT:PSS; conductivity.odp)
D_ION = 1e-12            # m^2/s, order-of-magnitude PEDOT ionic diffusivity


def _stack(sign: float, with_encap: bool) -> list:
    """Beam layers (bottom->top) with unit free strain per alpha*Q_areal.

    Active electrodes carry +-1/h (sign sets the polarity); passive Nafion
    electrolyte and optional outer encapsulation carry zero free strain.
    """
    layers = []
    if with_encap:
        layers.append(Layer("enc_b", H_ENC_BOT, E_N, 0.0))
    layers.append(Layer("bot", H_BOT, E_P, +sign / H_BOT))
    layers.append(Layer("naf", H_NAF, E_N, 0.0))
    layers.append(Layer("top", H_TOP, E_P, -sign / H_TOP))
    if with_encap:
        layers.append(Layer("enc_t", H_ENC_TOP, E_N, 0.0))
    return layers


def beam_factor(with_encap: bool = True) -> float:
    """G with kappa = alpha * Q_areal * G. ``with_encap`` adds the 0.4 um Nafion
    encapsulation on both faces (the true 5-layer device); False reproduces the
    paper's 3-layer convention."""
    return multilayer_curvature(_stack(+1.0, with_encap))


def polarity_ratio(with_encap: bool = True) -> float:
    """|kappa(+V)| / |kappa(-V)| for the asymmetric stack (linear beam)."""
    kp = multilayer_curvature(_stack(+1.0, with_encap))
    km = multilayer_curvature(_stack(-1.0, with_encap))
    return abs(kp) / abs(km)


def fresh_device() -> pd.DataFrame:
    """Per-staircase-voltage charge (DMM) and deflection swing (manual) for the
    fresh device (2026-05-06). Up-sweep voltages 0.2/0.4/0.6/0.8 V."""
    relays = [(50, 100, 0.2), (150, 200, 0.4), (250, 300, 0.6), (350, 400, 0.8),
              (450, 500, 0.6), (550, 600, 0.4), (650, 700, 0.2)]
    runs = [(SESS / "2026-05-06_13-20-47_step_voltage_relay2_750s", 6998),
            (SESS / "2026-05-06_16-34-21_step_voltage_relay2_750s", 6999)]
    rows = []
    for d, num in runs:
        r = pd.read_csv(d / "readings.csv")
        t = r["time"].to_numpy(float)
        i = r["dmm2_voltage"].to_numpy(float) / SHUNT
        m = pd.read_csv(MANUAL / f"{num}.csv", header=None, names=["t", "dx", "dy"])
        tt = m["t"].to_numpy(float)
        th = np.arctan(m["dy"].to_numpy(float) / m["dx"].to_numpy(float))
        for tc, to, V in relays:
            base = np.median(i[(t >= tc - 5) & (t < tc - 1)])
            on = (t >= tc) & (t < to)
            q = np.trapezoid(i[on] - base, t[on])
            rest = th[(tt >= tc - 5) & (tt < tc - 1)]
            chg = th[(tt >= to - 6) & (tt < to - 1)]
            sw = np.median(chg) - np.median(rest) if len(rest) > 2 and len(chg) > 2 else np.nan
            rows.append(dict(run=num, V=V, Q_uC=q * 1e6, sw_mrad=sw * 1000))
    F = pd.DataFrame(rows)
    return (
        F[F["V"].isin([0.2, 0.4, 0.6, 0.8])]
        .groupby("V", as_index=False)
        .agg(Q_uC=("Q_uC", "mean"), sw_mrad=("sw_mrad", "mean"))
    )


def freq_response() -> pd.DataFrame:
    """Sine-sweep deflection amplitude vs frequency (2026-06-09)."""
    d = SESS / "2026-06-09_16-16-55_moku_freqsweep_sine_0p5v"
    cfg = json.loads((d / "config.json").read_text())
    w = pd.read_csv(d / "moku_waveform.csv", usecols=["time"])
    tr = pd.read_csv(sorted(d.glob("*_opencv.csv"))[0])
    tr = tr[tr["match_score"] > 0.4]
    tt = tr["time_s"].to_numpy(float)
    th = np.arctan(tr["displacement_y_px"].to_numpy(float) / tr["displacement_x_px"].to_numpy(float))
    rows = []
    for g in cfg["moku_waveform_generator_stages"]:
        a, b, f = g["start_time"], g["end_time"], g["frequency_hz"]
        mid = a + (b - a) * 0.5
        s = th[(tt >= mid) & (tt < b)]
        s = s[np.isfinite(s)]
        amp = (np.percentile(s, 97) - np.percentile(s, 3)) / 2 * 1000 if len(s) > 10 else np.nan
        rows.append(dict(f=f, defl_mrad=amp))
    return pd.DataFrame(rows).dropna()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    G5 = beam_factor(with_encap=True)    # true 5-layer device
    G3 = beam_factor(with_encap=False)   # paper's 3-layer convention
    F = fresh_device()

    # C_v from Q-V slope (series areal cap of two asymmetric electrodes; encap is
    # passive -> does not affect the electrical capacitance)
    cA_per_cv = H_TOP * H_BOT / (H_TOP + H_BOT)
    slope_QV = np.polyfit(F["V"], F["Q_uC"] * 1e-6, 1)[0]   # C/V = C_cell
    Cv = slope_QV / (cA_per_cv * AREA)                      # F/m^3

    # alpha from theta-Q slope (theta = kappa*L/2 convention)
    slope_thQ = np.polyfit(F["Q_uC"] * 1e-6, F["sw_mrad"] / 1000, 1)[0]  # rad/C
    alpha5 = abs(slope_thQ / (G5 * L / 2.0 / AREA))   # encapsulation-corrected (true)
    alpha3 = abs(slope_thQ / (G3 * L / 2.0 / AREA))   # 3-layer apparent (paper convention)
    alpha = alpha5
    encap_penalty = 1.0 - abs(G5) / abs(G3)           # bending lost to encapsulation

    # t_l and inferred sigma_e (fresh fast/slow ends, and degraded)
    def sigma_for(tl):  # S/cm
        return (Cv / tl) * L**2 / 100.0
    pol = polarity_ratio(with_encap=True)

    # frequency fit: A(f) = A0 / sqrt(1 + (2 pi f tau)^2)
    fr = freq_response()
    def lp(f, A0, tau):
        return A0 / np.sqrt(1 + (2 * np.pi * f * tau) ** 2)
    p, _ = curve_fit(lp, fr["f"], fr["defl_mrad"], p0=[fr["defl_mrad"].max(), 0.5],
                     bounds=([0, 1e-3], [1e3, 100]))
    A0, tau_fit = p
    fc = 1.0 / (2 * np.pi * tau_fit)

    print("=== reduced-order model fit (fresh device 2026-05-06) ===")
    print(F.to_string(index=False))
    print(f"\nC_v   = {Cv/1e6:6.1f} F/cm^3   (lit 38)        from Q-V slope {slope_QV*1e6:.2f} uC/V")
    print(f"alpha (5-layer, encap-corrected) = {alpha5:.3e} m^3/C")
    print(f"alpha (3-layer, paper convention) = {alpha3:.3e} m^3/C   (lit 0.44-0.69e-10)")
    print(f"encapsulation stiffening: bending -{encap_penalty*100:.0f}% per unit strain (|G3|/|G5|={abs(G3)/abs(G5):.2f})")
    print(f"polarity |k(+)|/|k(-)| = {pol:.4f}  -> electrode-thickness asymmetry gives "
          f"{'SYMMETRIC' if abs(pol-1)<1e-6 else 'asymmetric'} response (robust to encap)")
    # FORWARD t_l from the *measured* conductivities (not inverted)
    tl_pristine = (Cv / (SIGMA_PRISTINE * 100)) * L**2
    tl_dmso = (Cv / (SIGMA_MEASURED * 100)) * L**2
    tau_ion = lambda h: h**2 / D_ION
    print(f"t_l(sigma=0.5 S/cm pristine) = {tl_pristine:.1f} s  (paper's electronic-limited regime)")
    print(f"t_l(sigma=500 S/cm MEASURED) = {tl_dmso*1e3:.1f} ms  -> ~{0.5/tl_dmso:.0f}-{1.9/tl_dmso:.0f}x faster than the ~0.5-1.9 s response")
    print(f"  => electronic transport is NOT rate-limiting for this DMSO device")
    print(f"ion penetration into electrode tau=h^2/D: h=0.53um -> {tau_ion(H_BOT):.2f} s, h=1.16um -> {tau_ion(H_TOP):.2f} s (matches response)")
    print(f"[withdrawn] inverted sigma_e if electronic-limited: {sigma_for(1.9):.1f}-{sigma_for(0.5):.1f} S/cm (~100x below measured 500 -> premise false)")
    print(f"frequency fit: tau={tau_fit:.2f} s, f_c={fc:.2f} Hz, A0={A0:.1f} mrad (ionic/mechanical)")

    # ---- figures ----
    # Q-V
    fig, ax = plt.subplots(figsize=(6.5, 4.6))
    ax.plot(F["V"], F["Q_uC"], "o", ms=8, color="#1f77b4", label="fresh device (2026-05-06)")
    vv = np.linspace(0, 0.85, 50)
    ax.plot(vv, slope_QV * 1e6 * vv + np.polyfit(F["V"], F["Q_uC"], 1)[1], "--", color="#888",
            label=f"C_v = {Cv/1e6:.0f} F/cm³ (lit 38)")
    ax.set_xlabel("Applied voltage (V)"); ax.set_ylabel("Transported charge (µC)")
    ax.set_title("Charge–voltage: volumetric capacitance"); ax.grid(alpha=0.3); ax.legend()
    fig.tight_layout()
    for e in ("png", "svg"): fig.savefig(OUT / f"fig_model_charge_voltage.{e}", dpi=110)
    plt.close(fig)

    # theta-Q
    fig, ax = plt.subplots(figsize=(6.5, 4.6))
    ax.plot(F["Q_uC"], F["sw_mrad"], "o", ms=8, color="#2ca02c", label="fresh device")
    qq = np.linspace(0, F["Q_uC"].max() * 1.05, 50)
    ax.plot(qq, slope_thQ * 1e-6 * 1000 * qq, "--", color="#888",
            label=f"α = {alpha5*1e11:.2f}×10⁻¹¹ m³/C (encap-corrected)")
    ax.set_xlabel("Transported charge (µC)"); ax.set_ylabel("Deflection swing (mrad)")
    ax.set_title("Strain–charge coupling (beam-model inversion)"); ax.grid(alpha=0.3); ax.legend()
    fig.tight_layout()
    for e in ("png", "svg"): fig.savefig(OUT / f"fig_model_strain_charge.{e}", dpi=110)
    plt.close(fig)

    # frequency fit
    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.plot(fr["f"], fr["defl_mrad"], "o", color="#1f77b4", label="deflection (2026-06-09)")
    ff = np.logspace(np.log10(fr["f"].min()), np.log10(fr["f"].max()), 200)
    ax.plot(ff, lp(ff, A0, tau_fit), "-", color="#d62728",
            label=f"first-order, τ={tau_fit:.2f} s (f₋₃dB={fc:.2f} Hz)")
    ax.axvline(fc, color="#999", ls=":", lw=1)
    ax.set_xscale("log"); ax.set_xlabel("Frequency (Hz)"); ax.set_ylabel("Deflection amplitude (mrad)")
    ax.set_title("Mechanical frequency response vs first-order model"); ax.grid(alpha=0.3, which="both"); ax.legend()
    fig.tight_layout()
    for e in ("png", "svg"): fig.savefig(OUT / f"fig_model_frequency_fit.{e}", dpi=110)
    plt.close(fig)

    pd.DataFrame([
        dict(parameter="C_v (F/cm^3)", fitted=round(Cv / 1e6, 1), literature="38"),
        dict(parameter="alpha 5-layer encap-corrected (m^3/C)", fitted=f"{alpha5:.2e}", literature="0.44e-10..0.69e-10 (3-layer)"),
        dict(parameter="alpha 3-layer paper-convention (m^3/C)", fitted=f"{alpha3:.2e}", literature="0.44e-10..0.69e-10"),
        dict(parameter="encapsulation bending penalty", fitted=f"-{encap_penalty*100:.0f}%", literature="not modelled (paper or here-3L)"),
        dict(parameter="sigma_e (4-probe, measured S/cm)", fitted=f"~{SIGMA_MEASURED:.0f}", literature="0.5 (pristine); 850 (PH1000+DMSO datasheet)"),
        dict(parameter="t_l at measured sigma_e (ms)", fitted=round(tl_dmso * 1e3, 1), literature="vs ~500-1900 ms response -> NOT rate-limiting"),
        dict(parameter="tau_ion h^2/D electrode (s)", fitted=f"{tau_ion(H_BOT):.2f}-{tau_ion(H_TOP):.2f}", literature="matches observed response (ionic limit)"),
        dict(parameter="tau_mech (s)", fitted=round(tau_fit, 2), literature="ionic/mechanical, not electronic"),
        dict(parameter="polarity ratio (model, with encap)", fitted=round(pol, 4), literature="1.0 (symmetric)"),
        dict(parameter="polarity ratio (measured)", fitted="~1.2", literature="—"),
    ]).to_csv(OUT / "reduced_order_fit_params.csv", index=False)
    print(f"\nWrote figures + reduced_order_fit_params.csv to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
