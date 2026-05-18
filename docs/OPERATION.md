# Operation And Automation Contract

This document describes the current behavior that matters for running experiments by hand or by an AI/scripted agent.

## Control Model

The FastAPI backend is the single control plane. The browser UI, HTTP agents, and helper scripts all start and stop runs through the same backend controller.

- Human UI start: `POST /api/start_measurement` with `control_source: "ui"`.
- Agent preset start: `POST /api/experiment_configs/start/{file_name}?control_source=agent`.
- Stop: `POST /api/stop_measurement?control_source=ui|agent|api|script`.
- Status: `GET /api/status`.
- Live backfill: `GET /api/current_session/data?limit=6000`.

The browser polls `/api/status`. If an agent starts a run, a human can open `http://localhost:3000` mid-run and see the active config, control source, runtime log, elapsed time, and current plots from the same backend session. The frontend also backfills recent in-memory samples from `/api/current_session/data`.

## Human And Agent Run Commands

Start the services:

```bash
OPEN_BROWSER=0 ./start.sh
```

Run a saved preset through the app HTTP API:

```bash
uv run python3 scripts/run_experiment_config_http.py \
  step_voltage_relay2_750s_oscilloscope.json \
  --leave-services-running
```

The agent script starts missing app/camera services unless `--no-start-services` is supplied. It uses the preset endpoint above, so it behaves like loading a saved config in the browser and clicking Start.

## Measurement Timing

The run clock `t0` is defined inside `MeasurementController.start_measurement`, after instruments are connected, the camera has been prepared if enabled, the oscilloscope has been started if oscilloscope mode is enabled, and the configured ready delay has elapsed.

Before `t0`:

1. A session folder is created and `config.json` is saved.
2. Instruments are connected and configured.
3. If `record_camera` is true, the camera service prepares the Canon EDSDK session without starting recording.
4. If `measurement_source` is `oscilloscope`, the scope acquisition is started before the ready delay so it is already running at `t0`.
5. `camera_ready_delay_seconds` is applied as a general hardware ready delay, not only a camera delay.

At `t0`:

1. The backend records `Measurement t0`.
2. If camera recording is enabled, the camera start command is issued asynchronously.
3. Voltage acquisition starts.
4. Voltage and relay schedules start from elapsed time 0.

At stop:

1. Oscilloscope stop and camera stop are scheduled together when both are active.
2. Voltage and relay control tasks are cancelled.
3. DMM/readings logging is finalized.
4. Oscilloscope waveforms are exported when in oscilloscope mode.
5. Instruments are disconnected.
6. If auto-download is enabled for a camera run, the post-run video transfer task starts after the measurement has fully stopped.

## Sync Acceptance

For 50 fps video, one frame is 0.02 s. Treat camera/electrical sync as acceptable only when the measured start/stop offset is less than 20 ms.

The backend logs camera timing details into the session `log.txt` and exposes them through `/api/status`:

- request received time at the camera service
- daemon command write/flush time
- daemon command received/completed time
- HTTP elapsed time
- acknowledgement offset relative to measurement `t0`

If the logged camera start or stop offset exceeds 20 ms, do not treat the video/electrical trace as frame-synchronized for analysis until the cause is investigated.

## Measurement Sources

### DMM Mode

`readings.csv` is the primary electrical output. It contains:

```text
time,dmm1_voltage,dmm2_voltage,sample_index,read_duration_ms,loop_duration_ms,late_by_ms,overrun
```

DMM mode is suitable for slow checks and calibration. It is not suitable for sharp relay-switching current peaks.

### Oscilloscope Mode

`oscilloscope_waveform.csv` is the primary electrical output. `readings.csv` is timing-only in this mode and does not contain the high-rate CH1/CH2 waveform values.

The waveform CSV columns are:

```text
time,scope_time,ch1_voltage,ch2_voltage,sample_index,ch1_sample_index,ch2_sample_index
```

Use CH1 for applied voltage. Use CH2 as shunt voltage. For the current channel:

```text
current_mA = ch2_voltage / shunt_ohms * 1000
```

The plotting helper defaults to a 330 ohm shunt:

```bash
uv run python3 scripts/plot_oscilloscope_waveform.py \
  user-data/sessions/<session>/oscilloscope_waveform.csv \
  --analysis-output user-data/sessions/<session>/oscilloscope_waveform_analysis.svg
```

The Tektronix full-record setup is configured by the backend. The configured record span includes the ready delay plus the planned run duration so the exported data can cover `t=0` through the stop time.

## Camera And Video Output

The camera records to its SD card. After the run, the download helper releases the camera service session, finds the newest movie on the camera, stores the raw camera movie in:

```text
user-data/big-videos/
```

It then creates an H.264 MP4 at CRF 22 in the session folder. Raw-download metadata is written next to the raw file as JSON.

Manual video transfer:

```bash
uv run python3 scripts/download_latest_camera_recording.py --session-dir user-data/sessions/<session>
```

Manual conversion of existing MOV files:

```bash
uv run python3 scripts/convert_mov_to_mp4.py user-data/sessions/<session> --crf 22
```

Auto-download runs only when both of these config fields are true:

```json
{
  "record_camera": true,
  "auto_download_camera_recording": true
}
```

The full-run presets in `user-data/experiment-configs/` enable both fields.

## Session Outputs

Each run creates a folder under `user-data/sessions/` unless `ECA_DATA_DIR` is set.

Typical DMM-mode session:

```text
user-data/sessions/<timestamp>_<test_name>/
  readings.csv
  config.json
  log.txt
  <camera>.mp4                 # if video was downloaded/converted
```

Typical oscilloscope-mode session:

```text
user-data/sessions/<timestamp>_<test_name>/
  readings.csv                 # timing/status rows, not high-rate scope data
  oscilloscope_waveform.csv
  oscilloscope_waveform_metadata.json
  config.json
  log.txt
  <camera>.mp4                 # if video was downloaded/converted
```

Raw camera movies are intentionally kept outside the session folder:

```text
user-data/big-videos/<camera-file>.MOV
user-data/big-videos/<camera-file>.MOV.json
```

## Important Presets

- `step_voltage_relay2_750s.json`: DMM-based 750 s full run.
- `step_voltage_relay2_750s_oscilloscope.json`: oscilloscope-based 750 s full run.
- `scope_0p2v_relay2_single_pulse_test.json`: short oscilloscope/relay test preset.

The two full-run presets currently use camera recording, auto-stop at 750 s, and auto-download/compress after the run.
