# Camera Service

The camera implementation for the Canon 2000D DSLR lives under `camera/`.

## Components

### C++ Camera Control
- `camera/CameraControl.cpp` - Linux EDSDK bridge used by the webapp.
- `camera/StartRecord.cpp` / `camera/StopRecord.cpp` - Older one-shot examples used by the previous LabVIEW workflow.

`CameraControl` can run as a long-lived daemon. The webapp prepares that daemon before the experiment clock starts, then sends the record command at the measurement `t0` boundary where electrical acquisition and staged controls start.

### Python HTTP Bridge
- `camera/camera_service.py` - FastAPI service that exposes HTTP endpoints for camera control

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
If `camera/CameraControl` is not built, the service runs in mock mode:
```bash
cd camera
python camera_service.py
```

### Production Mode
After compiling `CameraControl`:
```bash
cd camera
python camera_service.py
```

The service will automatically detect the executable and use it.

## API Endpoints

- `GET /status` - Get camera status
- `POST /prepare` - Open the EDSDK session before measurement start
- `POST /start_record` - Start recording
- `POST /stop_record` - Stop recording
- `POST /release` - Close the EDSDK camera session so file-transfer tools can access the SD card
- `GET /health` - Health check

## Downloading the Latest Recording

Video files are saved on the camera SD card. After a measurement has stopped,
download the newest movie into `user-data/big-videos/` and create a CRF 22
H.264 MP4 in the target session folder:

```bash
uv run python3 scripts/download_latest_camera_recording.py --session-dir user-data/sessions/<session>
```

Use `--dry-run` to preview the selected camera file and destination without
copying. The script writes a JSON sidecar next to the raw movie with the source
URI, camera timestamp, and local path. If the camera is visible as an unmounted
GVFS/gphoto volume, the script will mount it automatically before copying.

The main app can also trigger this path with:

```http
POST /api/download_latest_camera_recording
GET /api/download_latest_camera_recording/status
```

For config-driven auto-download, both `record_camera` and
`auto_download_camera_recording` must be true.

## Port

The camera service runs on port 8001 by default.

## Notes

1. The camera must be connected via USB and powered on
2. Ensure the Canon EDSDK shared library/DLL is available to the executable
3. The camera should be set to Movie mode
4. Video files are saved to the camera's SD card
