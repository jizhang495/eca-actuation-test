# Quick Start Guide

Get the ECA Testing Webapp running in 5 minutes!

## Prerequisites

- [uv](https://github.com/astral-sh/uv) package manager installed
- Node.js 18+ installed
- VISA drivers installed (optional for development mode)

## Automated Setup (Recommended)

### Windows

Open PowerShell in project directory:

```powershell
.\start.ps1
```

### Linux/Mac

Open terminal in project directory:

```bash
chmod +x start.sh
./start.sh
```

The script will:
1. Check dependencies
2. Install Python packages
3. Install Node packages
4. Start all services
5. Open browser automatically

## Manual Setup

### 1. Install Dependencies

#### Backend
```bash
cd eca-actuation-test
uv sync
```

#### Frontend
```bash
cd frontend
npm install
```

### 2. Start Services

Open 3 separate terminals:

#### Terminal 1: Backend
```bash
cd eca-actuation-test
uv run run_backend.py
```

#### Terminal 2: Camera Service (optional)
```bash
cd camera
uv run camera_service.py
```

#### Terminal 3: Frontend
```bash
cd frontend
npm run dev
```

### 3. Open Browser

Navigate to: `http://localhost:3000`

## First Test

1. Click **"Start Measurement"** (will run in mock mode without instruments)
2. Watch the graphs update in real-time
3. Click **"Stop Measurement"** to save data
4. Check `data/` folder for saved session

## Connect Real Instruments

### 1. Find Available Instruments

Once the app is running, the dropdowns will show:
- Available VISA resources (DMMs, Power Supply)
- Available serial ports (Relay Board)

### 2. Select Instruments

- Choose VISA ID for DMM1
- Choose VISA ID for DMM2
- Choose VISA ID for Power Supply
- Choose Serial Port for Relay Board

### 3. Configure Test

#### Add Voltage Stages
1. Click "Add Stage" under DC Power Supply
2. Set start time, end time, and voltage
3. Add up to 10 stages

#### Add Relay Stages
1. Click "Add Stage" under Relay Channel 1/2
2. Set start time, end time, and state (open/closed)
3. Add up to 10 stages per channel

### 4. Run Measurement

1. Enter test name
2. Configure stages
3. Click "Start Measurement"
4. Monitor live graphs
5. Click "Stop Measurement" when done

## Data Output

Each measurement creates a folder in `data/`:

```
data/2025-10-13_14-30-15_test1/
├── readings.csv    # DMM readings
├── config.json     # Test configuration
└── log.txt        # Session log
```

## Troubleshooting

### Services Won't Start

**Check ports are not in use:**
```bash
# Windows
netstat -ano | findstr :8000
netstat -ano | findstr :3000

# Linux/Mac
lsof -i :8000
lsof -i :3000
```

**Kill existing processes:**
```bash
# Windows
taskkill /PID <PID> /F

# Linux/Mac
kill <PID>
```

### Camera Not Available

This is normal! The app works in **mock mode** without the camera. Camera is optional for testing.

To enable camera:
1. Compile C++ executables (see `camera/README.md`)
2. Connect Canon camera via USB
3. Restart camera service

### No Instruments Detected

App will run in **mock mode** for development. To connect real instruments:

1. **Install VISA drivers**
   - Download NI-VISA from ni.com
   - Install and restart

2. **Connect instruments via USB**
   - Power on all instruments
   - Wait for USB drivers to install

3. **Verify connection**
   - Check in NI MAX (Windows)
   - Or refresh the webapp

4. **Restart backend**
   - Stop backend (Ctrl+C)
   - Restart: `uv run run_backend.py`

### WebSocket Connection Failed

1. Ensure backend is running on port 8000
2. Check browser console for errors
3. Clear browser cache
4. Try different browser

### Frontend Build Errors

```bash
cd frontend
rm -rf node_modules .next
npm install
npm run dev
```

## Next Steps

1. **Read Full Documentation**: [README.md](README.md)
2. **Setup Guide**: [SETUP.md](SETUP.md)
3. **API Documentation**: Visit `http://localhost:8000/docs`
4. **PRD**: [docs/PRD.md](docs/PRD.md)

## Development Tips

### Backend Only

Just need API access? Run only the backend:

```bash
cd eca-actuation-test
uv run run_backend.py
```

API will be available at `http://localhost:8000`

### Frontend Only

Already have backend running? Run only frontend:

```bash
cd frontend
npm run dev
```

### Hot Reload

Both backend and frontend support hot reload:
- Backend: Edit Python files, server auto-restarts
- Frontend: Edit React components, page auto-refreshes

### API Testing

Interactive API docs at: `http://localhost:8000/docs`

Try endpoints directly in the browser!

## Common Use Cases

### Quick Test with Mock Data

1. Start services
2. Click "Start Measurement"
3. Wait 10 seconds
4. Click "Stop Measurement"
5. Check `data/` folder

### Test Voltage Stages

1. Add 3 voltage stages:
   - 0-5s: 0.2V
   - 5-10s: 0.4V
   - 10-15s: 0.6V
2. Start measurement
3. Watch power supply change voltage

### Test Relay Switching

1. Add relay stages to Channel 1:
   - 0-5s: closed
   - 5-10s: open
   - 10-15s: closed
2. Start measurement
3. Watch relay indicator

### Record Full Experiment

1. Connect all instruments
2. Set up voltage and relay stages
3. Enter meaningful test name
4. Start measurement
5. Let run for desired duration
6. Stop measurement
7. Review data in `data/` folder

## Performance Notes

- **Sampling Rate**: Default 10 Hz, adjustable 1-100 Hz
- **Graph Update**: 10 Hz via WebSocket
- **Max Data Points**: 500 displayed (all logged to CSV)
- **File Size**: ~10 KB per minute at 10 Hz

## Stopping Services

### Automated Stop (Recommended)

**Windows:**
```powershell
.\stop.ps1
```

**Linux/Mac:**
```bash
chmod +x stop.sh
./stop.sh
```

These scripts will:
- Stop all Python processes (backend, camera service)
- Stop all Node.js processes (frontend)
- Verify all ports are free
- Show status of each service

### Manual Stop Commands

**Windows:**
```powershell
taskkill /IM python.exe /F
taskkill /IM node.exe /F
```

**Linux/Mac:**
```bash
pkill -f "python.*run_backend.py"
pkill -f "python.*camera_service.py"
pkill -f "npm run dev"
```

### Manual Stop (Terminal Method)

Press `Ctrl+C` in each terminal window where services are running.

---

**You're ready to start testing!** 🚀

For detailed documentation, see [README.md](README.md)

