# Electrochemical Actuator Testing Webapp Requirements

This document describes the current product requirements and implementation target for the ECA testing app. See [OPERATION.md](OPERATION.md) for the runtime contract used during actual experiments.

## 1. Vision

The app provides a single control surface for electrochemical actuator experiments. It must be usable in two equivalent ways:

1. A human opens the browser, configures a run, clicks `Start`, monitors progress, and clicks `Stop`.
2. An AI agent or script sends the same commands through the backend HTTP API while a human can open the browser at any time and see the same live run state.

The browser is a monitor and operator console. It must not be the only way to run an experiment.

## 2. Supported Instruments

| Category | Model | Communication | Current role |
| --- | --- | --- | --- |
| DMM | Keithley 2110 x2 | VISA / USB | Slow voltage checks and DMM-mode logging |
| Oscilloscope | Tektronix | VISA / USB | Full-record voltage/current capture for relay-edge transients |
| Moku:Pro | Liquid Instruments Moku:Pro | MokuCLI / network or USB | Full-run CH1/CH2 high-rate voltage logging |
| Power supply | IT6412 | VISA / USB | Programmed voltage stages |
| Relay board | Devantech USB-RLY08C | USB serial | Programmed relay stages |
| Camera | Canon 2000D DSLR | Canon EDSDK bridge + HTTP service | Synchronized video recording |
| Future | RS Pro RSDG805 | VISA / USB | Function generator replacement for some supply workflows |

## 3. Core Requirements

### 3.1 Human and Agent Control

- The frontend and automation scripts must control the same backend state through HTTP endpoints.
- Agent-triggered runs must be visible in the browser without restarting the frontend.
- Saved experiment configs must be startable through `POST /api/experiment_configs/start/{file_name}`.
- The agent control path should behave like a human clicking `Start` with a loaded config.
- Run status must be available from `GET /api/status`.
- Recent live data must be available from `GET /api/current_session/data` so a browser opened mid-run can backfill the graph.

### 3.2 Timing and Sync

- The measurement clock `t0` is defined by the backend, not the frontend.
- Instrument connection, camera preparation, high-rate measurement setup, and the configured ready delay occur before `t0`.
- At `t0`, camera recording, voltage-stage scheduling, relay-stage scheduling, and live data logging start from the same backend clock.
- For 50 fps camera video, sync is acceptable when the measured camera/electrical start offset is less than one frame: `0.02 s`.
- Stop commands should stop electrical measurement and camera recording together. Any camera video transfer/compression happens only after measurement stop.
- Session logs should preserve enough timing metadata to audit sync after the run.

### 3.3 Electrical Measurement

- DMM mode writes slow continuous readings to `readings.csv`.
- Oscilloscope or Moku:Pro mode is the preferred mode for sharp current peaks at relay edges.
- Full-record Tektronix capture should start before `t0`, include the ready delay and planned run duration, stop with the measurement, and export the whole waveform to the session folder.
- Moku:Pro Data Logger capture should start before `t0`, crop pre-`t0` samples from the app CSV, stop with the measurement, and export full-run CH1/CH2 data to the session folder. `moku_sample_rate_hz` controls the Moku file rate separately from the app timing loop.
- The current analysis convention is `current_mA = ch2_voltage / 330 * 1000`.

### 3.4 Camera and Video

- Camera recording is optional per config through `record_camera`.
- If `record_camera` and `auto_download_camera_recording` are both true, the app should download and compress video after the run.
- Raw camera movies are stored under `user-data/big-videos/`.
- Converted MP4 files are stored in the session folder and encoded with H.264 CRF 22.
- The manual `Download & Convert` control should use the same conversion path as auto-download.

### 3.5 Output After a Run

Each run creates a session folder under `user-data/sessions/` unless `ECA_DATA_DIR` overrides it.

Expected artifacts:

```text
user-data/sessions/<timestamp>_<test_name>/
  config.json
  log.txt
  readings.csv
  oscilloscope_waveform.csv
  oscilloscope_waveform_metadata.json
  moku_waveform.csv
  moku_waveform_metadata.json
  oscilloscope_waveform.svg
  oscilloscope_waveform_analysis.svg
  <camera-recording>.mp4
```

Not every artifact exists for every run. DMM-only runs do not create high-rate waveform files. Runs without camera recording do not create video files.

## 4. UI Requirements

The header contains the main run controls:

- `Camera` checkbox
- `Ready delay (s)` input
- `Start`
- `Stop`

The configuration section should stay compact:

- `Save Config`
- `Load Config`
- `Auto-stop at (s)` checkbox and seconds input
- `Auto-download` checkbox
- `Download & Convert`

The UI should show:

- Camera service and recording status
- Active session id and elapsed time
- Measurement source selection: DMM, oscilloscope, or Moku:Pro
- Live plots for the two electrical channels
- Voltage and relay stage editors
- Current errors and recent log messages

## 5. Backend API Requirements

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/start_measurement` | `POST` | Start a run from a JSON config |
| `/api/stop_measurement` | `POST` | Stop the active run |
| `/api/status` | `GET` | Return live run, camera, and instrument status |
| `/api/current_session/data` | `GET` | Return recent live data for browser backfill |
| `/api/live` | `WS` | Stream live readings to the frontend |
| `/api/list_instruments` | `GET` | Return VISA and serial resources |
| `/api/experiment_configs/save` | `POST` | Save a config |
| `/api/experiment_configs/start/{file_name}` | `POST` | Start a saved config, including agent starts |
| `/api/download_latest_camera_recording` | `POST` | Download raw camera video and create MP4 |
| `/api/download_latest_camera_recording/status` | `GET` | Report video download/compression progress |

## 6. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| Sync | Camera/electrical start offset under 20 ms for 50 fps video |
| Reliability | A failed camera download must not corrupt electrical run data |
| Reproducibility | Save exact config JSON in every session folder |
| Monitoring | Browser can be opened during an agent-run experiment and recover current state |
| Data safety | Session outputs are written to a timestamped folder and raw large videos stay outside session folders |
| Extensibility | Instrument drivers remain modular for additional scopes, DAQs, supplies, and function generators |

## 7. Known Risks

- Camera sync is logged but not yet automatically rejected when it exceeds the 20 ms target.
- Manual camera download currently selects the newest camera movie; exact session correlation should be made stricter.
- DMM current acquisition is not adequate for fast relay-edge charge integration.
- Moku:Pro API control requires a working `mokucli` install and current MokuOS.
- There is no automated hardware-in-loop sync regression test.

The active issue list is maintained in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## 8. Future Extensions

1. Add explicit runtime warnings when measured camera/electrical sync exceeds 20 ms.
2. Add a hardware sync test using an LED or electrical trigger visible to both camera and scope.
3. Add additional high-rate acquisition backends such as DAQ, SMU, or potentiostat.
4. Add cross-session analysis for charge, strain, hysteresis, and repeatability.
5. Add stricter metadata linking between camera file, session id, and run timestamps.
