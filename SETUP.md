# Setup Guide

Detailed setup instructions for the ECA Testing Webapp.

## System Requirements

### Hardware
- Windows 10/11 (recommended) or Linux
- USB ports for instruments
- Minimum 4GB RAM
- 100MB free disk space

### Software
- Python 3.11 or higher
- Node.js 18 or higher
- VISA drivers (NI-VISA recommended)
- USB drivers for instruments

## Step-by-Step Setup

### 1. Install Python Dependencies

#### Option A: Using uv (Recommended)

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

#### Option B: Using pip

```bash
cd eca-actuation-test
pip install -e .
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

### 6. Compile Camera Executables (Optional)

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

### Development Mode

#### Terminal 1: Backend
```bash
cd eca-actuation-test
python run_backend.py
```

#### Terminal 2: Camera Service (Optional)
```bash
cd camera
python camera_service.py
```

#### Terminal 3: Frontend
```bash
cd frontend
npm run dev
```

### Access the Application

Open browser to: `http://localhost:3000`

## Testing Without Hardware

The system can run in development mode without physical instruments:

1. Start backend (will use mock instruments)
2. Start frontend
3. Use application normally
4. Data logging will work with simulated readings

## Troubleshooting

### "No module named 'pyvisa'"

```bash
pip install pyvisa pyvisa-py
```

### "VISA resource not found"

1. Check NI-VISA is installed
2. Verify instrument appears in NI MAX
3. Try disconnecting and reconnecting USB
4. Restart instrument

### "Camera service not available"

This is normal if camera is not compiled/connected. App works in mock mode.

### "Port 8000 already in use"

Kill existing process or change port in `run_backend.py`:
```python
uvicorn.run("app:app", host="0.0.0.0", port=8001)
```

### Frontend build errors

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
```

## Production Deployment

### Using Docker

Create `docker-compose.yml`:

```yaml
version: '3.8'
services:
  backend:
    build: ./eca-actuation-test
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
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

1. Read [README.md](README.md) for usage instructions
2. Check [docs/PRD.md](docs/PRD.md) for detailed specifications
3. Test with mock data first
4. Connect real instruments
5. Run calibration tests
6. Begin experiments

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

For sampling rates >10 Hz:
1. Use USB 3.0 ports
2. Close unnecessary applications
3. Increase buffer sizes in instrument drivers
4. Consider dedicated instrument PC

### Network Performance

For remote access:
1. Use gigabit Ethernet
2. Enable WebSocket compression
3. Reduce frontend graph data points
4. Consider local caching

## Backup and Recovery

### Data Backup

Regularly backup `data/` directory:
```bash
# Windows
xcopy /E /I data backup\data_YYYYMMDD

# Linux
rsync -av data/ backup/data_YYYYMMDD/
```

### Configuration Backup

Save instrument configurations:
- Export VISA settings from NI MAX
- Document relay board wiring
- Record camera settings
- Save stage configurations

---

**You're ready to start testing!**

