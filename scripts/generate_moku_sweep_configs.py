#!/usr/bin/env python3
"""Generate Moku waveform-characterization presets from the plan in downloads/.

Reproduces the published-style figure: a fixed-amplitude frequency sweep
(square/sine/triangle -> Bode-like theta_pp vs frequency) and a fixed-frequency
amplitude sweep (-> theta_pp vs voltage linearity), plus a CV-like triangular
scan-rate sweep.

Wiring (from the bench):
- Relay CH2 OPEN  -> actuator short-circuited (discharged / reset).
- Relay CH2 CLOSED -> actuator connected to the Moku Waveform Generator.

Each sweep is ONE continuous session: relay CH2 stays closed for the whole
sweep (so the actuator is driven continuously, like the reference figure) and
the waveform stages are tiled back-to-back with no discharge between
conditions. A short relay-open baseline brackets the sweep. Sessions are packed
toward, but kept under, the camera limit (< 720 s) so each video carries a full
sweep and there are fewer videos to motion-track.

SR551 differential current path (shunt 330 ohm, gain 10). Current input range
is 4 Vpp where square edges push current past the +/-121 uA (400 mVpp) ceiling,
else 400 mVpp for best small-signal resolution.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "user-data" / "experiment-configs"

BASELINE = 5.0     # relay open at start (actuator shorted) before the sweep
SETTLE = 0.5       # relay closed, settling, before the generator turns on
TAIL = 0.5         # relay stays closed after the generator turns off
END_BUFFER = 2.0
MAX_SESSION = 720.0
MAX_STAGES = 30

# Frequencies in the reference frequency-sweep figure (Hz).
FREQS = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 2, 3, 4, 5, 6, 7, 8, 10]
# Amplitude sweep at 0.1 Hz: +/-0.1 .. +/-0.8 V (Vpp = 2 * amplitude).
AMP_VPPS = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6]

# Per-frequency stage length: at least MIN_CYCLES cycles and at least MIN_SECONDS,
# which yields ~equal-width blocks at higher frequency like the reference figure.
MIN_CYCLES = 3
MIN_SECONDS = 20.0


def freq_duration(freq: float) -> float:
    return max(MIN_CYCLES / freq, MIN_SECONDS)


def build_continuous(conditions: list[dict]) -> tuple[list[dict], list[dict], float]:
    """Tile conditions back-to-back; relay CH2 closed for the whole window."""
    wavegen: list[dict] = []
    t = BASELINE + SETTLE
    for cond in conditions:
        d = cond["duration_s"]
        wavegen.append(
            {
                "start_time": round(t, 4),
                "end_time": round(t + d, 4),
                "waveform": cond["waveform"],
                "vpp": cond["vpp"],
                "frequency_hz": cond["frequency_hz"],
            }
        )
        t += d
    wavegen_end = t
    relay = [
        {"start_time": BASELINE, "end_time": round(wavegen_end + TAIL, 4), "state": "closed"}
    ]
    session_end = round(wavegen_end + TAIL + END_BUFFER, 1)
    return wavegen, relay, session_end


def freq_sweep(waveform: str, vpp: float) -> list[dict]:
    return [
        {"waveform": waveform, "vpp": vpp, "frequency_hz": float(f), "duration_s": freq_duration(f)}
        for f in FREQS
    ]


def amp_sweep(waveform: str, freq: float, cycles: int) -> list[dict]:
    return [
        {"waveform": waveform, "vpp": v, "frequency_hz": freq, "duration_s": cycles / freq}
        for v in AMP_VPPS
    ]


def cv_sweep(amplitude_v: float, scan_rates_mv_s: list[float], cycles: int) -> list[dict]:
    # scan_rate = 4 * amplitude * frequency  ->  frequency = scan_rate / (4 * amplitude)
    out = []
    for sr in scan_rates_mv_s:
        freq = (sr / 1000.0) / (4.0 * amplitude_v)
        out.append(
            {
                "waveform": "Ramp",
                "vpp": round(2.0 * amplitude_v, 4),
                "frequency_hz": round(freq, 6),
                "duration_s": cycles / freq,
            }
        )
    return out


def make_config(
    name: str,
    conditions: list[dict],
    current_range: str,
    sample_rate: float,
    record_camera: bool = True,
) -> dict:
    wavegen, relay, end = build_continuous(conditions)
    if len(wavegen) > MAX_STAGES:
        raise ValueError(f"{name}: {len(wavegen)} stages exceeds {MAX_STAGES}")
    if end >= MAX_SESSION:
        raise ValueError(f"{name}: session {end:.1f}s exceeds {MAX_SESSION}s")
    return {
        "test_name": name,
        "measurement_source": "moku",
        "dmm1_visa_id": None,
        "dmm2_visa_id": None,
        "oscilloscope_visa_id": None,
        "moku_address": "default",
        "power_supply_visa_id": None,
        "relay_port": "default",
        "voltage_stages": [],
        "relay_ch1_stages": [],
        "relay_ch2_stages": relay,
        "moku_waveform_generator_stages": wavegen,
        "sampling_rate_hz": 10.0,
        "moku_sample_rate_hz": float(sample_rate),
        "moku_current_mode": "sr551_differential",
        "current_shunt_ohms": 330.0,
        "current_amplifier_gain": 10.0,
        "moku_current_input_range": current_range,
        "dmm_acquisition_mode": "low_noise",
        "stop_after_seconds": end,
        "record_camera": record_camera,
        "auto_download_camera_recording": record_camera,
        "camera_ready_delay_seconds": 2.0,
    }


# name -> dict(conditions, range, rate, [camera])
SPECS = {
    # Electrical pre-flight (no camera): sine/square/triangle at one safe point.
    "moku_first_compare_0p5v_0p1hz": dict(
        conditions=[
            {"waveform": "Sine", "vpp": 1.0, "frequency_hz": 0.1, "duration_s": 30.0},
            {"waveform": "Square", "vpp": 1.0, "frequency_hz": 0.1, "duration_s": 30.0},
            {"waveform": "Ramp", "vpp": 1.0, "frequency_hz": 0.1, "duration_s": 30.0},
        ],
        current_range="4Vpp",
        sample_rate=10000.0,
        record_camera=False,
    ),
    # Frequency sweeps at +/-0.5 V (Vpp 1.0) -> Bode-like theta_pp(f). One per waveform.
    "moku_freqsweep_sine_0p5v": dict(conditions=freq_sweep("Sine", 1.0), current_range="400mVpp", sample_rate=2000.0),
    "moku_freqsweep_square_0p5v": dict(conditions=freq_sweep("Square", 1.0), current_range="4Vpp", sample_rate=10000.0),
    "moku_freqsweep_triangle_0p5v": dict(conditions=freq_sweep("Ramp", 1.0), current_range="400mVpp", sample_rate=2000.0),
    # Amplitude sweeps at 0.1 Hz, +/-0.1..+/-0.8 V -> linearity theta_pp(V). One per waveform.
    "moku_ampsweep_sine_0p1hz": dict(conditions=amp_sweep("Sine", 0.1, 5), current_range="400mVpp", sample_rate=2000.0),
    "moku_ampsweep_square_0p1hz": dict(conditions=amp_sweep("Square", 0.1, 5), current_range="4Vpp", sample_rate=10000.0),
    "moku_ampsweep_triangle_0p1hz": dict(conditions=amp_sweep("Ramp", 0.1, 5), current_range="400mVpp", sample_rate=2000.0),
    # CV-like triangular scan-rate sweep at +/-0.8 V (20/50/100 mV/s).
    "moku_cv_triangle_0p8v": dict(conditions=cv_sweep(0.8, [100.0, 50.0, 20.0], 2), current_range="400mVpp", sample_rate=2000.0),
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"{'preset':34s} {'stages':6s} {'dur(s)':8s} {'range':8s} {'rate':6s} cam")
    for name, spec in SPECS.items():
        cfg = make_config(name, **spec)
        (OUT / f"{name}.json").write_text(json.dumps(cfg, indent=2) + "\n")
        print(
            f"{name:34s} {len(cfg['moku_waveform_generator_stages']):<6d} "
            f"{cfg['stop_after_seconds']:<8.1f} {cfg['moku_current_input_range']:8s} "
            f"{int(cfg['moku_sample_rate_hz']):<6d} {cfg['record_camera']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
