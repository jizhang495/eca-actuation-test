# Camera Service

This directory contains the camera control system for the Canon 2000D DSLR.

## Components

### C++ Camera Control
- `CameraControl.cpp` - Linux EDSDK bridge used by the webapp.
- `StartRecord.cpp` / `StopRecord.cpp` - Older one-shot examples used by the previous LabVIEW workflow.

`CameraControl` can run as a long-lived daemon. The webapp prepares that daemon before the experiment clock starts, then sends the record command at the same boundary where DMM acquisition starts.

### Python HTTP Bridge
- `camera_service.py` - FastAPI service that exposes HTTP endpoints for camera control

## Compilation (Linux)

Place the Canon EDSDK under:

```bash
camera/EDSDK/EDSDKv132010L/Linux/EDSDK
```

Then build:

```bash
cd camera
./build_camera.sh
```

This creates `camera/CameraControl` and copies `libEDSDK.so` next to it. `./start.sh` also runs this build automatically when the SDK folder is present and the binary is missing or stale.

Test detection:

```bash
cd camera
./CameraControl detect
```

If Linux has mounted the camera through `gvfsd-gphoto2`, the service will try to unmount that `gphoto2://...` mount before opening the EDSDK session.

## Compilation (Windows Legacy)

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
After compiling `CameraControl`:
```bash
python camera_service.py
```

The service will automatically detect the executable and use it.

## API Endpoints

- `GET /status` - Get camera status
- `POST /prepare` - Open the EDSDK session before measurement start
- `POST /start_record` - Start recording
- `POST /stop_record` - Stop recording
- `GET /health` - Health check

## Port

The camera service runs on port 8001 by default.

## Notes

1. The camera must be connected via USB and powered on
2. Ensure the Canon EDSDK shared library/DLL is available to the executable
3. The camera should be set to Movie mode
4. Video files are saved to the camera's SD card
