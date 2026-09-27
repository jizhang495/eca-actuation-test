# Nine-run actuator sequence

Load the eight presets in order **0 → 7 → 0** for each actuator. Add the device ID to the test
name when saving a session. All presets enable camera recording; automatic
video download is off. Total recording time: **93 min 14 s**.

| Run | Preset | Measurement | Duration | Drive source | Corresponding previous preset | Changes from previous preset |
| --- | --- | --- | ---: | --- | --- | --- |
| **0** | [0_step_p0p4v_7x50s_750s.json](0_step_p0p4v_7x50s_750s.json) | Seven +0.4 V steps; primary charging/bending timescale | 750 s | IT6412 | [steps/step_0p5v_moku.json](../steps/step_0p5v_moku.json) | +0.5 → +0.4 V; source now 0 V at 0–45 s and 705–750 s, +0.4 V at 45–705 s. Seven 50 s relay pulses unchanged. Current-input range 400 mVpp → 4 Vpp for switching-peak headroom. |
| **1** | [1_step_neg0p4v_7x50s_750s.json](1_step_neg0p4v_7x50s_750s.json) | Seven −0.4 V steps; opposite polarity | 750 s | IT6412 | [steps/step_neg0p5v_moku.json](../steps/step_neg0p5v_moku.json) | −0.5 → −0.4 V; source now 0 V at 0–45 s and 705–750 s, −0.4 V at 45–705 s. Seven 50 s relay pulses unchanged. Current-input range 400 mVpp → 4 Vpp for switching-peak headroom. |
| **2** | [2_freqsweep_sine_0p2vpp_458s.json](2_freqsweep_sine_0p2vpp_458s.json) | Sine frequency sweep: 0.2 Vpp = ±0.1 V, 0.05–10 Hz | 458 s | Moku Output 1 | [sweeps/1_moku_freqsweep_sine_0p5v.json](../sweeps/1_moku_freqsweep_sine_0p5v.json) | All 20 output stages: 1.0 → 0.2 Vpp (±0.5 → ±0.1 V). Frequencies/timing unchanged; current-input range remains 400 mVpp. |
| **3** | [3_freqsweep_sine_0p8vpp_458s.json](3_freqsweep_sine_0p8vpp_458s.json) | Sine frequency sweep: 0.8 Vpp = ±0.4 V, 0.05–10 Hz | 458 s | Moku Output 1 | [sweeps/1_moku_freqsweep_sine_0p5v.json](../sweeps/1_moku_freqsweep_sine_0p5v.json) | All 20 output stages: 1.0 → 0.8 Vpp (±0.5 → ±0.4 V). Frequencies/timing unchanged. Current-input range remains 400 mVpp. |
| **4** | [4_ampsweep_sine_0p1hz_408s.json](4_ampsweep_sine_0p1hz_408s.json) | Sine amplitude sweep: 0.2–1.6 Vpp (±0.1–0.8 V), 0.1 Hz | 408 s | Moku Output 1 | [sweeps/4_moku_ampsweep_sine_0p1hz.json](../sweeps/4_moku_ampsweep_sine_0p1hz.json) | Current-input range remains 400 mVpp. Output amplitudes, frequency and timing unchanged. |
| **5** | [5_cv_triangle_0p8v_520s.json](5_cv_triangle_0p8v_520s.json) | Triangular I–V: ±0.8 V, 100/50/20 mV/s | 520 s | Moku Output 1 | [sweeps/7_moku_cv_triangle_0p8v.json](../sweeps/7_moku_cv_triangle_0p8v.json) | Current-input range remains 400 mVpp. Triangle amplitude, scan rates and timing unchanged. |
| **6** | [6_ladder_p0p2_to_p0p8v_750s.json](6_ladder_p0p2_to_p0p8v_750s.json) | Positive ladder: +0.2/+0.4/+0.6/+0.8/+0.6/+0.4/+0.2 V | 750 s | IT6412 | [step_voltage_relay2_750s_moku.json](../step_voltage_relay2_750s_moku.json) | Current-input range 400 mVpp → 4 Vpp for switching-peak headroom. Positive up/down voltage ladder and 50 s hold/discharge timing unchanged. |
| **7** | [7_ladder_neg0p2_to_neg0p8v_750s.json](7_ladder_neg0p2_to_neg0p8v_750s.json) | Negative ladder: −0.2/−0.4/−0.6/−0.8/−0.6/−0.4/−0.2 V | 750 s | IT6412 | [step_voltage_relay2_750s_moku-negative.json](../step_voltage_relay2_750s_moku-negative.json) | Current-input range 400 mVpp → 4 Vpp for switching-peak headroom. Negative up/down voltage ladder and 50 s hold/discharge timing unchanged. |
| **8 (repeat 0)** | [0_step_p0p4v_7x50s_750s.json](0_step_p0p4v_7x50s_750s.json) | Closing +0.4 V reference; identical to run 0 | 750 s | IT6412 | Preset 0 above | Reload preset 0 without changing its settings; use a distinct closing-session name. No separate preset file. |

All eight preset `test_name` values were renamed to match their numbered files.
The table lists every other changed configuration field. Session durations,
sample rates, camera settings, relay timing, shunt and amplifier gain are
unchanged from the corresponding previous presets. Historical files are preserved.

The 0.4 V operating amplitude aligns with the voltage ladder; the ±0.1 V
sweep checks amplitude dependence. Square/step drives (presets 0, 1, 6 and 7,
including the closing repeat of 0) use **4 Vpp current-input range** because
the 3L step recording clipped repeatedly at 400 mVpp. Sine and triangle drives
(presets 2–5) retain **400 mVpp** for small-current resolution; manually select
4 Vpp if those runs approach or exceed the input range.
The new zero-voltage source stages in runs 0/1 bracket the pulse sequence;
relay-controlled drive/discharge intervals remain unchanged.

- **Source changes:** after run 1, IT6412 → Moku Output 1; after run 5,
  Moku Output 1 → IT6412. Switch physical connections with outputs off.
- Runs 0, 1 and 8: seven 50 s holds with 50 s discharge intervals. Run 8
  reuses preset 0 to check drift/deterioration during characterization. Give
  the closing session a distinct name, such as `D-A1_R08_reference`.
- Runs 6 and 7: separate positive and negative up-and-down voltage ladders,
  retaining the original seven 50 s holds and 50 s discharge intervals.
- Moku records all runs: DC steps at 10 kSa/s, waveforms at 2 kSa/s.
  Current conversion assumes a 330 Ω shunt and SR551 gain ×10, differential
  outputs. Current-input range is **4 Vpp for presets 0/1/6/7** and
  **400 mVpp for presets 2–5**. Inspect both CH2 and CH3; widen the range
  on sine/triangle runs if peaks approach ±200 mV. Record the range used.
  Input range and waveform output Vpp are different settings.
- Fixed polarity order applies to all devices. Keep orientation and conditioning
  consistent, record recovery/history, and verify actual voltage/current/video.
  The presets do not automatically establish full recovery or equilibrium.

See [the experiment plan](../../reports/PLAN.md) for fabrication allocation,
measurement handling and analysis. Configurations are schema/timing checked;
bench validation is pending.
