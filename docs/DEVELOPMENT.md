# Development Guide

This guide covers local development, mock instruments, and frontend-specific notes. For experiment-running behavior, use [OPERATION.md](OPERATION.md).

## Local Services

The app has three local services:

| Service | Default URL | Command |
| --- | --- | --- |
| Backend | `http://localhost:8000` | `cd eca-actuation-test && uv run run_backend.py` |
| Camera service | `http://localhost:8001` | `cd camera && uv run camera_service.py` |
| Frontend | `http://localhost:3000` | `cd frontend && npm run dev` |

Use the startup script for the normal path:

```bash
OPEN_BROWSER=0 ./start.sh
```

## Frontend

The frontend is a Next.js 14 app using TypeScript, Tailwind CSS, shadcn/ui, Recharts, and lucide-react icons.

Important files:

```text
frontend/src/app/page.tsx
frontend/src/components/DMMGraph.tsx
frontend/src/components/VoltageStageConfigurator.tsx
frontend/src/components/RelayStageConfigurator.tsx
frontend/src/lib/expression.ts
frontend/next.config.js
```

Development commands:

```bash
cd frontend
npm install
npm run dev
npm run build
```

The frontend proxies `/api/*` requests to `http://localhost:8000` through `frontend/next.config.js`. The WebSocket connects directly to `ws://localhost:8000/api/live`, so update both places if the backend port changes.

The UI keeps about 6000 live points in memory. Change `MAX_DATA_POINTS` in `frontend/src/app/page.tsx` if needed.

## Mock Instruments

The app can run without physical hardware. If no real VISA resources are found, the backend exposes mock devices.

| Instrument | Mock address | Purpose |
| --- | --- | --- |
| DMM1 | `MOCK::DMM::DMM1::INSTR` | Simulated voltage reading |
| DMM2 | `MOCK::DMM::DMM2::INSTR` | Simulated voltage reading |
| Oscilloscope | `MOCK::SCOPE::OSC1::INSTR` | Simulated CH1/CH2 waveform export |
| Moku:Pro | `MOKU::MOCK::PRO` | Simulated CH1/CH2 logger export |
| Power supply | `MOCK::POWER::IT6412::INSTR` | Simulated voltage output |
| Relay board | `MOCK_COM3` | Simulated relay switching |
| Camera | mock service mode | Simulated camera status |

Mock mode is useful for:

- Running the full UI without instruments
- Testing saved configs
- Checking session output creation
- Exercising voltage and relay schedules
- Developing browser/agent control paths

Basic mock test:

```bash
OPEN_BROWSER=0 ./start.sh
```

Then open `http://localhost:3000`, click `Start`, wait a few seconds, click `Stop`, and inspect `user-data/sessions/`.

Mock instrument behavior lives in:

```text
eca-actuation-test/instruments/mock.py
```

## API Development

Interactive API docs are available when the backend is running:

```text
http://localhost:8000/docs
```

Useful endpoints:

```text
GET  /health
GET  /api/status
GET  /api/list_instruments
GET  /api/current_session/data?limit=6000
POST /api/start_measurement
POST /api/stop_measurement
POST /api/experiment_configs/save
POST /api/experiment_configs/start/{file_name}
```

The agent/script path should use saved config starts when possible:

```bash
uv run python3 scripts/run_experiment_config_http.py \
  step_voltage_relay2_750s_oscilloscope.json \
  --leave-services-running
```

## Validation

Common checks:

```bash
uv run python3 -m py_compile \
  eca-actuation-test/api_models.py \
  eca-actuation-test/measurement_controller.py \
  eca-actuation-test/app.py \
  camera/camera_service.py \
  scripts/run_experiment_config_http.py

PYTHONPATH=eca-actuation-test uv run python3 - <<'PY'
from pathlib import Path
from api_models import MeasurementConfig

for path in sorted(Path('user-data/experiment-configs').glob('*.json')):
    MeasurementConfig.model_validate_json(path.read_text(encoding='utf-8'))
    print(f'valid {path}')
PY

cd frontend
npm run build
```

## Development Risks

- `stop.sh` and `stop.ps1` use broad process matching for Python and Node processes. They are convenient on a dedicated lab machine, but be careful on a shared development computer.
- The Docker examples in [SETUP.md](SETUP.md) are starting points only; this repo does not currently include production Dockerfiles.
- Mock data verifies software flow, not real hardware timing, electrical noise, camera sync, or oscilloscope configuration.
