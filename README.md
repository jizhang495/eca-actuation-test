# ECA Testing Webapp

**Electrochemical Actuator Testing and Control System**

A unified web interface for controlling, monitoring, and recording experiments involving electrochemical actuators (ECAs). Coordinates multiple laboratory instruments including digital multimeters, DC power supplies, relay boards, and cameras for precise, reproducible actuator measurements.

## Features

- **Real-time Data Visualization**: Live voltage graphs from two DMMs with adjustable scales
- **Synchronized Control**: Coordinated control of power supply, relays, and camera
- **Programmable Stages**: Configure up to 10 voltage/relay stages with precise timing
- **Data Logging**: Automatic CSV logging with timestamped sessions
- **REST API**: Full API access for automation and integration
- **WebSocket Streaming**: Real-time data push for live monitoring
- **Modern UI**: Built with React, Next.js, and shadcn/ui components

## Architecture

### Backend (Python + FastAPI)
- **FastAPI** server with REST and WebSocket endpoints
- **Instrument drivers** for DMMs, power supply, relay board
- **Data logger** for CSV recording and session management
- **Camera controller** bridge to C++ camera service
- **Async scheduler** for precise timing control

### Frontend (Next.js + React)
- **Next.js** with TypeScript
- **shadcn/ui** component library
- **Recharts** for live data visualization
- **WebSocket client** for real-time updates

### Camera Service (C++ + Python)
- **C++ executables** for Canon camera control
- **Python HTTP bridge** for API integration

## Supported Instruments

| Instrument | Model | Communication | Purpose |
|------------|-------|---------------|---------|
| DMM ×2 | Keithley 2110 | VISA/USB | Voltage measurement |
| Power Supply | IT6412 | VISA/USB | Programmable voltage output |
| Relay Board | Devantech USB-RLY08C | USB Serial | Channel switching |
| Camera | Canon 2000D DSLR | USB (via C++ SDK) | Video recording |

## Prerequisites

### Backend
- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager (or pip)
- VISA drivers (NI-VISA or compatible)
- USB drivers for instruments

### Frontend
- Node.js 18 or higher
- npm or yarn

### Camera (Optional)
- Canon EDSDK (for camera control)
- C++ compiler (Visual Studio or MinGW on Windows)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/eca-actuation-test.git
cd eca-actuation-test
```

### 2. Backend Setup

Using uv (recommended):

```bash
# Install dependencies
uv sync

# Or using pip
pip install -e .
```

### 3. Frontend Setup

```bash
cd frontend
npm install
```

### 4. Camera Service Setup (Optional)

See [camera/README.md](camera/README.md) for compilation instructions.

## Quick Start

### 1. Start Backend Server

```bash
# From project root
cd eca-actuation-test
python app.py
```

The backend will start on `http://localhost:8000`

### 2. Start Camera Service (Optional)

In a separate terminal:

```bash
cd camera
python camera_service.py
```

The camera service will start on `http://localhost:8001`

### 3. Start Frontend

In another terminal:

```bash
cd frontend
npm run dev
```

The frontend will start on `http://localhost:3000`

### 4. Open Browser

Navigate to `http://localhost:3000`

## Usage

### Basic Workflow

1. **Connect Instruments**
   - Connect DMMs, power supply, and relay board via USB
   - Ensure instruments are powered on
   - The webapp will auto-detect available VISA resources

2. **Configure Test**
   - Enter a test name
   - Select VISA IDs for DMMs and power supply
   - Select serial port for relay board
   - Set sampling rate (default: 10 Hz)

3. **Set Up Stages** (Optional)
   - Add voltage stages for power supply (up to 10)
   - Add relay stages for channels 1 and 2 (up to 10 each)
   - Configure start time, end time, and values

4. **Start Measurement**
   - Click "Start Measurement"
   - Camera begins recording (if available)
   - DMMs start logging data
   - Voltage/relay stages execute automatically

5. **Monitor Progress**
   - Watch live voltage graphs
   - Check camera recording status
   - View elapsed time

6. **Stop Measurement**
   - Click "Stop Measurement"
   - Camera stops recording
   - Data is saved to CSV
   - Session folder created in `data/`

### Data Output

Each measurement session creates a timestamped folder in `data/`:

```
data/
  └── 2025-10-13_14-30-15_test1/
      ├── readings.csv      # Time, DMM1, DMM2 voltages
      ├── config.json       # Test configuration
      ├── log.txt           # Session log
      └── video.mp4         # Camera recording (manual transfer)
```

## API Documentation

### REST Endpoints

#### Start Measurement
```http
POST /api/start_measurement
Content-Type: application/json

{
  "config": {
    "test_name": "test1",
    "dmm1_visa_id": "USB0::0x05E6::0x2110::...",
    "dmm2_visa_id": "USB0::0x05E6::0x2110::...",
    "power_supply_visa_id": "USB0::...",
    "relay_port": "COM3",
    "voltage_stages": [
      {"start_time": 0, "end_time": 5, "voltage": 0.2},
      {"start_time": 5, "end_time": 10, "voltage": 0.4}
    ],
    "relay_ch1_stages": [
      {"start_time": 0, "end_time": 5, "state": "closed"}
    ],
    "relay_ch2_stages": [],
    "sampling_rate_hz": 10
  }
}
```

#### Stop Measurement
```http
POST /api/stop_measurement
```

#### Get Status
```http
GET /api/status
```

#### List Instruments
```http
GET /api/list_instruments
```

#### List Sessions
```http
GET /api/sessions
```

#### Get Session Data
```http
GET /api/session/{session_id}/data
```

### WebSocket

Connect to `ws://localhost:8000/api/live` for real-time DMM readings:

```javascript
const ws = new WebSocket('ws://localhost:8000/api/live');
ws.onmessage = (event) => {
  const reading = JSON.parse(event.data);
  // { time: 1.23, dmm1_voltage: 0.45, dmm2_voltage: 0.67 }
};
```

## Development Mode

The system can run in development mode without physical instruments:

- **Mock VISA resources**: Backend will simulate instruments if none detected
- **Mock camera**: Camera service runs in mock mode without C++ executables
- **Data logging**: CSV logging still works with simulated data

## Project Structure

```
eca-actuation-test/
├── camera/                    # Camera control C++ code and service
│   ├── StartRecord.cpp
│   ├── StopRecord.cpp
│   ├── camera_service.py
│   └── README.md
├── eca-actuation-test/        # Python backend
│   ├── instruments/           # Instrument drivers
│   │   ├── dmm.py
│   │   ├── power_supply.py
│   │   └── relay_board.py
│   ├── app.py                 # FastAPI application
│   ├── api_models.py          # Pydantic models
│   ├── camera_controller.py   # Camera service bridge
│   ├── data_logger.py         # Data logging
│   └── measurement_controller.py
├── frontend/                  # Next.js frontend
│   ├── src/
│   │   ├── app/              # Next.js app directory
│   │   ├── components/       # React components
│   │   │   ├── ui/          # shadcn/ui components
│   │   │   ├── DMMGraph.tsx
│   │   │   ├── VoltageStageConfigurator.tsx
│   │   │   └── RelayStageConfigurator.tsx
│   │   └── lib/             # Utilities
│   ├── package.json
│   └── next.config.js
├── data/                      # Measurement data (auto-created)
├── docs/                      # Documentation
│   └── PRD.md                # Product Requirements Document
├── labview/                   # Legacy LabVIEW code
├── pyproject.toml            # Python dependencies
└── README.md                 # This file
```

## Troubleshooting

### VISA Connection Issues

If instruments are not detected:

1. Install NI-VISA or compatible driver
2. Verify instruments appear in NI MAX (National Instruments Measurement & Automation Explorer)
3. Check USB connections
4. Restart instruments

### Camera Not Available

The webapp works without the camera in mock mode. To use real camera:

1. Compile C++ executables (see [camera/README.md](camera/README.md))
2. Ensure Canon EDSDK DLLs are in PATH
3. Connect camera via USB and power on
4. Start camera service

### Port Already in Use

If port 8000 or 3000 is busy:

- Backend: Edit `app.py` and change `uvicorn.run(app, port=XXXX)`
- Frontend: Edit `package.json` dev script: `next dev -p XXXX`

### WebSocket Connection Failed

Ensure backend is running on `localhost:8000` before starting frontend.

## Performance

- **DMM Sampling**: Up to 100 Hz per channel (configurable)
- **WebSocket Update**: 10 Hz default
- **Data Logging**: Buffered writes, no data loss
- **Timing Precision**: <10 ms drift for voltage/relay stages

## Safety Notes

⚠️ **Important Safety Considerations:**

1. **Voltage Limits**: Verify voltage stages are within safe limits for your setup
2. **Current Protection**: Always set appropriate current limits on power supply
3. **Emergency Stop**: Stop button immediately halts all operations
4. **Relay Ratings**: Do not exceed relay board specifications
5. **Supervision**: Always supervise experiments, especially with new configurations

## Future Enhancements

- [ ] Oscilloscope integration
- [ ] Function generator support
- [ ] Cloud data logging
- [ ] Multi-session comparison and analytics
- [ ] Real-time impedance measurements
- [ ] AI-driven experiment optimization

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

See [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check [docs/PRD.md](docs/PRD.md) for detailed specifications

## Acknowledgments

- Built with FastAPI, Next.js, and shadcn/ui
- Instrument control via PyVISA and pyserial
- Camera control via Canon EDSDK

---

**Made for electrochemical actuator research and testing**
