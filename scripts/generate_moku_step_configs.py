#!/usr/bin/env python3
"""Generate constant-voltage step presets (one per voltage).

Each preset holds the IT6412 power supply at one fixed voltage while relay CH2
is cycled: CLOSED connects the actuator to the supply (a voltage step from the
shorted state), OPEN short-circuits the actuator (discharge / reset). So each
run is a series of identical step/discharge cycles at one voltage, for
averaging the step response and charge transfer.

The Moku logs CH1 voltage and the SR551 differential current (shunt 330 ohm,
gain 10) at the 400 mVpp input range -- the small-current regime where the
preamp earns its resolution; measured step transients (~104 uA) sit under the
+/-121 uA ceiling. Sessions are kept under the camera limit (< 720 s).
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "user-data" / "experiment-configs" / "steps"

VOLTAGES = [0.5, 0.6, 0.7, 0.8, -0.5, -0.6, -0.7, -0.8]

T_START = 50.0    # initial relay-open baseline (actuator shorted)
T_ON = 50.0       # relay closed: actuator driven at the step voltage
T_OFF = 50.0      # relay open: actuator shorted / discharged
N_CYCLES = 7
STOP_AFTER = 750.0   # auto-stop, matching the parent 750 s step test


def relay_cycles() -> list[dict]:
    relay = []
    t = T_START
    for _ in range(N_CYCLES):
        relay.append(
            {"start_time": round(t, 1), "end_time": round(t + T_ON, 1), "state": "closed"}
        )
        t += T_ON + T_OFF
    return relay


def name_for(voltage: float) -> str:
    magnitude = f"{abs(voltage):g}".replace(".", "p")
    return f"step_{'neg' if voltage < 0 else ''}{magnitude}v_moku"


def make_config(voltage: float) -> dict:
    relay = relay_cycles()
    return {
        "test_name": name_for(voltage),
        "measurement_source": "moku",
        "dmm1_visa_id": None,
        "dmm2_visa_id": None,
        "oscilloscope_visa_id": None,
        "moku_address": "default",
        "power_supply_visa_id": "default",
        "relay_port": "default",
        "voltage_stages": [{"start_time": 0.0, "end_time": STOP_AFTER, "voltage": voltage}],
        "relay_ch1_stages": [],
        "relay_ch2_stages": relay,
        "moku_waveform_generator_stages": [],
        "sampling_rate_hz": 10.0,
        "moku_sample_rate_hz": 10000.0,
        "moku_current_mode": "sr551_differential",
        "current_shunt_ohms": 330.0,
        "current_amplifier_gain": 10.0,
        "moku_current_input_range": "400mVpp",
        "dmm_acquisition_mode": "low_noise",
        "stop_after_seconds": STOP_AFTER,
        "record_camera": True,
        "auto_download_camera_recording": False,
        "camera_ready_delay_seconds": 2.0,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for voltage in VOLTAGES:
        cfg = make_config(voltage)
        (OUT / f"{cfg['test_name']}.json").write_text(json.dumps(cfg, indent=2) + "\n")
        print(
            f"{cfg['test_name']:22s} V={voltage:+.1f}  cycles={N_CYCLES} "
            f"stop={cfg['stop_after_seconds']:.0f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
