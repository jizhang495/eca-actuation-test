# ECA Testing Webapp

Electrochemical actuator experiment control system for synchronized electrical measurement, voltage/relay actuation, and optional camera recording.

The app is designed for two equivalent control paths:

- A human controls a run from the browser at `http://localhost:3000`.
- An AI agent or script controls the same backend through HTTP while a human can open the browser anytime to monitor progress.

The backend is the shared control plane. See [docs/OPERATION.md](docs/OPERATION.md) for the current timing, sync, automation, and output contract.

## What It Controls

| Instrument | Model | Purpose |
| --- | --- | --- |
| DMM x2 | Keithley 2110 | Slow voltage checks and DMM-mode logging |
| Oscilloscope | Tektronix TBS 2000B series | Full-record CH1/CH2 capture for relay-edge transients |
| Moku:Pro | Liquid Instruments Moku:Pro | Full-run CH1/CH2 high-rate voltage logging and optional Waveform Generator stages |
| Power supply | IT6412 | Programmed voltage stages |
| Relay board | Devantech USB-RLY08C | Programmed relay switching |
| Camera | Canon 2000D DSLR | Synchronized video recording |

## Quick Start

```bash
OPEN_BROWSER=0 ./start.sh
```

Services:

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- Camera service: `http://localhost:8001`

Run a saved preset through the same HTTP path used by the browser:

```bash
uv run python3 scripts/run_experiment_config_http.py \
  step_voltage_relay2_750s_oscilloscope.json \
  --leave-services-running
```

Moku:Pro preset:

```bash
uv run python3 scripts/run_experiment_config_http.py \
  step_voltage_relay2_750s_moku.json \
  --leave-services-running
```

Open `http://localhost:3000` during the run to monitor the active config, elapsed time, live data, camera state, and runtime events.

## Session Output

Runs create timestamped folders under `user-data/sessions/` unless `ECA_DATA_DIR` is set.

Typical outputs:

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

Not every run creates every file. DMM-only runs do not create oscilloscope waveform files. Runs without camera recording do not create video files. Raw camera movies are stored outside session folders under `user-data/big-videos/`; converted MP4s are stored in the session folder using H.264 CRF 22.

## Documentation

- [Quickstart](docs/QUICKSTART.md): shortest path to running the app
- [Setup](docs/SETUP.md): dependencies, drivers, ports, and hardware setup
- [Operation](docs/OPERATION.md): sync model, `t0`, agent/browser control, automation, and outputs
- [Camera](docs/CAMERA.md): Canon bridge, camera service, and video download/compression
- [Charge Transfer Analysis](docs/CHARGE_TRANSFER_ANALYSIS.md): current fitting, dual-exponential edge modeling, and whole-run charge plots
- [Development](docs/DEVELOPMENT.md): frontend notes, mock instruments, validation commands
- [Requirements](docs/REQUIREMENTS.md): current product requirements
- [Known Issues](docs/KNOWN_ISSUES.md): audit findings and fix candidates
- [LabVIEW Legacy Notes](docs/LABVIEW.md): previous LabVIEW implementation and hardware context

## Common Commands

```bash
# Backend only
cd eca-actuation-test
uv run run_backend.py

# Camera service only
cd camera
uv run camera_service.py

# Frontend only
cd frontend
npm run dev

# Plot oscilloscope waveform
uv run python3 scripts/plot_oscilloscope_waveform.py \
  user-data/sessions/<session>/oscilloscope_waveform.csv \
  --analysis-output user-data/sessions/<session>/oscilloscope_waveform_analysis.svg

# Plot un-plotted Moku sessions (newest-first; stops at the first one that already has a plot)
./plot-moku.sh

# Fit relay-edge current and plot whole-run charge transfer
uv run python3 scripts/analyze_charge_transfer.py \
  user-data/sessions/<session>
```

## Safety

- Verify voltage stages and current limits before starting hardware runs.
- Keep the power supply output off until the run starts.
- Supervise experiments, especially new configurations.
- Treat camera/electrical sync as acceptable for 50 fps video only when logged offset is under 20 ms.
