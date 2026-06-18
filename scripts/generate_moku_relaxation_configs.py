#!/usr/bin/env python3
"""Generate relaxation / charge-holding presets for the ECA.

These extend the driven sweeps and step tests with the *relaxation* regime --
what the actuator does when you stop driving it. Three families:

1. constant-V hold   -- hold one DC voltage with relay CH2 closed for a long
   time. The supply keeps sourcing the leakage current needed to hold V, so the
   steady-state current *is* the self-discharge rate; the camera shows mechanical
   creep at (maintained) constant charge. The run ends with a ~100 s short-circuit
   tail (CH2 open -> actuator across the 330 ohm): it discharges the held charge
   (= the full stored charge at V, since the supply kept it topped up), shows the
   mechanical recovery of theta, and its final rest vs the initial baseline rest
   flags any irreversible set.

2. open-circuit hold + reconnect -- charge, then isolate the actuator from the
   supply (relay CH1) while keeping it off the 330 ohm short (relay CH2 stays
   closed), hold open, then drop CH2 to discharge through the shunt and log the
   recovered charge. Charge retention measured mechanically (camera) during the
   hold and electrically (recovered charge) on reconnect.

3. discharge-R ladder -- the single-voltage relay step test repeated with a
   larger shunt/discharge resistor installed in place of the 330 ohm. The
   discharge tau = (R_s + R_ext)*C, so tau vs R_ext is a line whose slope is C and
   whose intercept is R_s*C: that separates the ~5.4 kOhm internal R_s (implied by
   the 104 uA / 0.6 V step peak) from C. Note the sense shunt also amplifies the
   reading (Moku sees I*shunt*gain), so a bigger shunt LOWERS the current ceiling
   (range_Vpp/(shunt*gain)); the current channel stays clean only to ~3.3 kOhm
   (on 4Vpp). For larger R_ext keep a separate big resistor in the discharge path
   with the 330 ohm sense shunt (current_shunt_ohms stays 330), or read the CH1
   voltage decay + camera instead of current.

RELAY WIRING (this rig, verify before trusting):
- relay CH2 ON ("closed") = actuator connected to supply (driven, NOT shorted);
  CH2 OFF = actuator across the 330 ohm shunt (discharge). Confirmed by data.
- relay CH1 ON ("closed") = actuator+shunt isolated from the supply;
  CH1 OFF (default) = connected. Per bench wiring.

MANUAL STEPS for the open-hold presets (the app cannot unplug a lead): the Moku
CH1 voltage tap is a ~1 MOhm drain across the actuator, so a true open circuit
needs it physically disconnected during the hold. Watch the runtime relay events
and unplug CH1 when CH1 isolates, replug before reconnect. See the plan md.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "user-data" / "experiment-configs" / "relaxation"


def vname(voltage: float) -> str:
    magnitude = f"{abs(voltage):g}".replace(".", "p")
    return f"{'neg' if voltage < 0 else ''}{magnitude}v"


def base_config(
    test_name: str,
    voltage_stages: list[dict],
    relay_ch1: list[dict],
    relay_ch2: list[dict],
    stop_after: float,
    shunt_ohms: float = 330.0,
    input_range: str = "400mVpp",
) -> dict:
    return {
        "test_name": test_name,
        "measurement_source": "moku",
        "dmm1_visa_id": None,
        "dmm2_visa_id": None,
        "oscilloscope_visa_id": None,
        "moku_address": "default",
        "power_supply_visa_id": "default",
        "relay_port": "default",
        "voltage_stages": voltage_stages,
        "relay_ch1_stages": relay_ch1,
        "relay_ch2_stages": relay_ch2,
        "moku_waveform_generator_stages": [],
        "sampling_rate_hz": 10.0,
        "moku_sample_rate_hz": 10000.0,
        "moku_current_mode": "sr551_differential",
        "current_shunt_ohms": shunt_ohms,
        "current_amplifier_gain": 10.0,
        "moku_current_input_range": input_range,
        "dmm_acquisition_mode": "low_noise",
        "stop_after_seconds": stop_after,
        "record_camera": True,
        "auto_download_camera_recording": False,
        "camera_ready_delay_seconds": 2.0,
    }


def constant_v_hold(
    voltage: float,
    baseline: float = 30.0,
    hold: float = 500.0,
    short: float = 100.0,
    settle: float = 5.0,
) -> dict:
    relay_close = baseline
    relay_open = baseline + hold
    end = relay_open + short
    # Supply reaches V `settle` s before CH2 closes and drops `settle` s after CH2
    # opens, so the relay never switches onto a slewing supply (and the discharge
    # tail starts from a settled state). No supply edge coincides with a relay edge.
    vs = [
        {"start_time": 0.0, "end_time": relay_close - settle, "voltage": 0.0},
        {"start_time": relay_close - settle, "end_time": relay_open + settle, "voltage": voltage},
        {"start_time": relay_open + settle, "end_time": end, "voltage": 0.0},
    ]
    # CH2 closed = drive/hold; when the stage ends CH2 goes OFF -> actuator across
    # the 330 ohm shunt = short-circuit discharge/hold for the tail.
    relay2 = [{"start_time": relay_close, "end_time": relay_open, "state": "closed"}]
    return base_config(f"hold_constV_{vname(voltage)}_{int(hold)}s_moku", vs, [], relay2, end)


def open_hold(
    voltage: float,
    hold: float,
    baseline: float = 20.0,
    charge: float = 30.0,
    readout: float = 50.0,
    settle: float = 5.0,
) -> dict:
    relay_close = baseline                  # CH2 closes onto an already-settled supply
    t_isolate = baseline + charge           # CH1 isolates supply; UNPLUG CH1 voltmeter here
    t_reconnect = t_isolate + hold          # CH2 drops -> discharge through shunt; REPLUG CH1 here
    end = t_reconnect + readout
    # Supply leads CH2-close by `settle` and drops `settle` after CH1 isolates, so
    # no supply edge coincides with a relay edge; the supply is moot once isolated.
    vs = [
        {"start_time": 0.0, "end_time": relay_close - settle, "voltage": 0.0},
        {"start_time": relay_close - settle, "end_time": t_isolate + settle, "voltage": voltage},
        {"start_time": t_isolate + settle, "end_time": end, "voltage": 0.0},
    ]
    # CH2 closed (un-shorted) through charge + hold, then OFF at reconnect -> discharge.
    relay2 = [{"start_time": relay_close, "end_time": t_reconnect, "state": "closed"}]
    # CH1 isolates supply from charge-end through the readout.
    relay1 = [{"start_time": t_isolate, "end_time": end, "state": "closed"}]
    return base_config(f"hold_open_{vname(voltage)}_{int(hold)}s_moku", vs, relay1, relay2, end)


def discharge_r_step(voltage: float, shunt_ohms: float) -> dict:
    # Single-voltage relay step test, identical schedule to the steps/ presets;
    # only the installed shunt/discharge resistor (and current_shunt_ohms) change.
    # A bigger shunt raises the per-amp Moku reading, so the current ceiling drops
    # (range_Vpp/(shunt*gain)); 330 ohm fits in 400mVpp, larger needs 4Vpp.
    input_range = "400mVpp" if shunt_ohms <= 330.0 else "4Vpp"
    t, relay2 = 50.0, []
    for _ in range(7):
        relay2.append({"start_time": round(t, 1), "end_time": round(t + 50.0, 1), "state": "closed"})
        t += 100.0
    vs = [{"start_time": 0.0, "end_time": 750.0, "voltage": voltage}]
    return base_config(
        f"dischargeR_{int(shunt_ohms)}ohm_{vname(voltage)}_moku",
        vs, [], relay2, 750.0, shunt_ohms=shunt_ohms, input_range=input_range,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    configs: list[dict] = []

    for v in (0.6, 0.8, -0.6, -0.8):
        configs.append(constant_v_hold(v))

    for hold in (60.0, 300.0, 600.0):
        configs.append(open_hold(0.6, hold))

    # Current-sense stays clean only to ~3.3 kOhm; tau-vs-R over 330..3300 already
    # gives C (slope) and R_s (intercept). Larger R_ext: separate discharge R +
    # 330 ohm sense, or CH1 voltage decay (see docstring / plan md).
    for shunt in (330.0, 1000.0, 3300.0):
        configs.append(discharge_r_step(0.6, shunt))

    for cfg in configs:
        (OUT / f"{cfg['test_name']}.json").write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"{cfg['test_name']:34s} stop={cfg['stop_after_seconds']:.0f}s shunt={cfg['current_shunt_ohms']:.0f}")
    print(f"\nwrote {len(configs)} presets to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
