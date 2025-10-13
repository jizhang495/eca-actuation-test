# Camera Service

This directory contains the camera control system for the Canon 2000D DSLR.

## Components

### C++ Camera Control
- `StartRecord.cpp` - Starts video recording on the camera
- `StopRecord.cpp` - Stops video recording on the camera

These programs use the Canon EDSDK (EOS Digital Software Development Kit) to control the camera.

### Python HTTP Bridge
- `camera_service.py` - FastAPI service that exposes HTTP endpoints for camera control

## Compilation (Windows)

To compile the C++ programs, you need:
1. Canon EDSDK installed
2. Visual Studio or MinGW compiler

Example compilation with Visual Studio:
```bash
cl StartRecord.cpp /I"path\to\EDSDK\Header" /link /LIBPATH:"path\to\EDSDK\Library" EDSDK.lib
cl StopRecord.cpp /I"path\to\EDSDK\Header" /link /LIBPATH:"path\to\EDSDK\Library" EDSDK.lib
```

## Running the Camera Service

### Development Mode (Mock)
If the C++ executables are not compiled, the service runs in mock mode:
```bash
python camera_service.py
```

### Production Mode
After compiling the C++ executables:
```bash
python camera_service.py
```

The service will automatically detect the executables and use them.

## API Endpoints

- `GET /status` - Get camera status
- `POST /start_record` - Start recording
- `POST /stop_record` - Stop recording
- `GET /health` - Health check

## Port

The camera service runs on port 8001 by default.

## Notes

1. The camera must be connected via USB and powered on
2. Ensure the Canon EDSDK DLLs are in the system PATH or same directory as executables
3. The camera should be set to Movie mode
4. Video files are saved to the camera's SD card

