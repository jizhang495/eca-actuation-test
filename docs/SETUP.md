# Setup Guide

Detailed setup instructions for the ECA Testing Webapp.

## System Requirements

### Hardware
- Linux or Windows 10/11
- USB ports for instruments
- Minimum 4GB RAM
- Free disk space for session CSVs and camera movies; long camera runs can be large

### Software
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js 18 or higher
- VISA drivers (NI-VISA recommended)
- USB drivers for instruments
- ffmpeg for MOV to MP4 conversion when camera download/compression is used
- MokuCLI and the Python `moku` package for Moku:Pro logging and output control. Follow Liquid Instruments' CLI install guide:
  `https://apis.liquidinstruments.com/cli/getting-started/install.html`

## Step-by-Step Setup

### 1. Install Python Dependencies

Install uv package manager:
```powershell
# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex
```

```bash
# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install project dependencies:
```bash
cd eca-actuation-test
uv sync
```

### 2. Install VISA Drivers

#### Windows

Download and install NI-VISA:
1. Go to [ni.com/en-us/support/downloads/drivers/download.ni-visa.html](https://www.ni.com/en-us/support/downloads/drivers/download.ni-visa.html)
2. Download NI-VISA Runtime
3. Run installer
4. Restart computer

#### Linux

Install python-vxi11 or use pyvisa-py:
```bash
pip install pyvisa-py
```

### 3. Install Frontend Dependencies

```bash
cd frontend
npm install
```

### 4. Verify Instrument Connections

#### Test VISA Connection

```python
import pyvisa
rm = pyvisa.ResourceManager()
print(rm.list_resources())
```

Should output something like:
```
('USB0::0x05E6::0x2110::1234567::INSTR', ...)
```

#### Test Serial Ports

```python
import serial.tools.list_ports
ports = serial.tools.list_ports.comports()
for port in ports:
    print(port.device)
```

Should output:
```
COM3
COM4
...
```

### 5. Configure Instruments

#### DMMs (Keithley 2110)
1. Connect via USB
2. Power on
3. Set to local mode (if remote mode is locked)
4. Verify in NI MAX (Windows) or using pyvisa

#### Power Supply (IT6412)
1. Connect via USB
2. Power on
3. Ensure output is OFF initially
4. Verify VISA connection

#### Oscilloscope (Tektronix)
1. Connect via USB or another VISA-supported transport
2. Power on and verify it appears in `pyvisa.ResourceManager().list_resources()`
3. Connect CH1 to the applied voltage signal
4. Connect CH2 to the current shunt voltage; the app converts current as `ch2_voltage / 330`
5. Set the probe attenuation on the scope to match the physical probe before a run

#### Moku:Pro
1. Connect Moku:Pro to the computer or network.
2. Install `mokucli` using Liquid Instruments' guide:
   `https://apis.liquidinstruments.com/cli/getting-started/install.html`
3. Run `uv sync` from the repo root. This installs the official Python `moku` package used for persistent Data Logger control.
4. Verify discovery:
   ```bash
   mokucli list
   ```
5. Update MokuOS if API commands report that the device is too old.
6. Download the local Moku:Pro instrument bitstreams once:
   ```bash
   mokucli instrument download 4.2.2 --hw-version mokupro
   ```
7. Connect Input 1 to the applied voltage signal using the 10x probe. The app treats Moku Input 1 as 10x and writes normalized circuit voltage to `moku_waveform.csv`.
8. Connect Input 2 to the current shunt voltage using 1x. The app converts current as `ch2_voltage / 330`.
9. If using the Moku output as the voltage source, wire Output 1 to the actuator drive node and the output reference/shield to circuit reference. When `moku_waveform_generator_stages` are configured, the app uses Moku Multi-Instrument Mode so the Waveform Generator can change Output 1 while the Data Logger records CH1/CH2.
10. In the app, choose `Moku:Pro` as the measurement source and select the discovered `MOKU::...` resource.
11. Set the Moku rate with `moku_sample_rate_hz`; the 750 s preset uses 10 kSa/s.

#### Relay Board (USB-RLY08C)
1. Connect via USB
2. Install USB-Serial driver if needed
3. Note COM port number
4. Verify serial connection

#### Camera (Canon 2000D)
1. Connect via USB
2. Power on
3. Set to Movie mode
4. Ensure SD card is inserted

### 6. Build Camera Bridge (Optional)

The current synchronized camera path uses the long-lived `CameraControl` daemon through `camera/camera_service.py`. `./start.sh` builds `camera/CameraControl` automatically when the Canon EDSDK folder is present and the binary is missing or stale.

On Linux, build manually with:

```bash
cd camera
./build_camera.sh
./CameraControl detect
```

`StartRecord.cpp` and `StopRecord.cpp` are legacy one-shot examples retained from the older workflow. They are not the preferred path for synchronized runs.

#### Windows with Visual Studio

```cmd
cd camera
cl StartRecord.cpp /I"C:\path\to\EDSDK\Header" /link /LIBPATH:"C:\path\to\EDSDK\Library" EDSDK.lib
cl StopRecord.cpp /I"C:\path\to\EDSDK\Header" /link /LIBPATH:"C:\path\to\EDSDK\Library" EDSDK.lib
```

#### Windows with MinGW

```bash
cd camera
g++ StartRecord.cpp -I"C:\path\to\EDSDK\Header" -L"C:\path\to\EDSDK\Library" -lEDSDK -o StartRecord.exe
g++ StopRecord.cpp -I"C:\path\to\EDSDK\Header" -L"C:\path\to\EDSDK\Library" -lEDSDK -o StopRecord.exe
```

Copy EDSDK DLLs to camera directory or add to PATH.

## Running the Application

The normal local setup uses three services:

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- Camera service: `http://localhost:8001`

The easiest path is:

```bash
OPEN_BROWSER=0 ./start.sh
```

Leave `OPEN_BROWSER=0` off if you want the script to open the browser automatically.

### Development Mode

#### Terminal 1: Backend
```bash
cd eca-actuation-test
uv run run_backend.py
```

#### Terminal 2: Camera Service (Optional)
```bash
cd camera
uv run camera_service.py
```

#### Terminal 3: Frontend
```bash
cd frontend
npm run dev
```

### Access the Application

Open browser to: `http://localhost:3000`

Agents should control runs through the same backend HTTP API that the browser uses. See [OPERATION.md](OPERATION.md) for the current automation contract, timing behavior, output files, and sync expectations.

## Testing Without Hardware

The system can run in development mode without physical instruments:

1. Start backend (will use mock instruments)
2. Start frontend
3. Use application normally
4. Data logging will work with simulated readings

## Troubleshooting

### "No module named 'pyvisa'"

```bash
cd eca-actuation-test
uv sync
```

### "VISA resource not found"

1. Check NI-VISA is installed
2. Verify instrument appears in NI MAX
3. Try disconnecting and reconnecting USB
4. Restart instrument

### "Camera service not available"

This is normal if camera is not compiled/connected. App works in mock mode.

### "Port 8000 already in use"

Kill the existing backend process or change the backend port in `run_backend.py`. Do not use port `8001`, because that is the camera service port. For example, use `8002`:
```python
uvicorn.run("app:app", host="0.0.0.0", port=8002)
```

Or run with different port:
```bash
cd eca-actuation-test
uv run python -c "import uvicorn; uvicorn.run('app:app', host='0.0.0.0', port=8002)"
```

If the backend port changes, also update the frontend rewrite target in `frontend/next.config.js`.

### Frontend build errors

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
```

## Production Deployment

### Docker

The repository does not currently include production Dockerfiles. Treat the following as a starting point only; it is not a ready-to-run deployment recipe.

Create `docker-compose.yml`:

```yaml
version: '3.8'
services:
  backend:
    build: ./eca-actuation-test
    ports:
      - "8000:8000"
    volumes:
      - ./user-data/sessions:/app/user-data/sessions
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0  # Relay board
  
  camera:
    build: ./camera
    ports:
      - "8001:8001"
    devices:
      - /dev/bus/usb:/dev/bus/usb  # USB devices
  
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend
```

Run:
```bash
docker-compose up
```

## Next Steps

1. Read [README.md](../README.md) for usage instructions
2. Read [OPERATION.md](OPERATION.md) for sync, automation, and run-output behavior
3. Check [KNOWN_ISSUES.md](KNOWN_ISSUES.md) before hardware runs
4. Test with mock data first
5. Connect real instruments
6. Run calibration tests
7. Begin experiments

## Support

If you encounter issues:
1. Check this guide
2. Review error messages in terminal
3. Check instrument connections
4. Verify VISA/serial drivers
5. Try mock mode to isolate hardware issues
6. Open an issue on GitHub

## Security Notes

⚠️ **Important:**
- Backend runs on `0.0.0.0` (all interfaces) for development
- In production, use firewall rules to restrict access
- Do not expose directly to internet without authentication
- Keep VISA drivers and firmware updated
- Use appropriate electrical safety measures

## Performance Tuning

### High-Speed Acquisition

For DMM sampling rates >10 Hz:
1. Use USB 3.0 ports
2. Close unnecessary applications
3. Increase buffer sizes in instrument drivers
4. Consider dedicated instrument PC

For relay-edge current peaks, use oscilloscope or Moku:Pro mode rather than relying on DMM acquisition. Tektronix mode writes `oscilloscope_waveform.csv`; Moku mode writes `moku_waveform.csv`.

### Network Performance

For remote access:
1. Use gigabit Ethernet
2. Enable WebSocket compression
3. Reduce frontend graph data points
4. Consider local caching

## Backup and Recovery

### Data Backup

Regularly backup the session data directory. By default this is `user-data/sessions/`; set `ECA_DATA_DIR` to store sessions elsewhere.
```bash
# Windows
xcopy /E /I user-data\sessions backup\sessions_YYYYMMDD

# Linux
rsync -av user-data/sessions/ backup/sessions_YYYYMMDD/
```

### Configuration Backup

Save instrument configurations:
- Export VISA settings from NI MAX
- Document relay board wiring
- Record camera settings
- Save stage configurations

---

**You're ready to start testing!**
