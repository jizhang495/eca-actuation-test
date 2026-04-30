"""
Camera Service HTTP Bridge

This service provides HTTP endpoints to control the Canon camera through the
Canon EDSDK CameraControl bridge.

In production, compile CameraControl and keep it running as a daemon. For
development, the service falls back to mock mode when the bridge is missing.
"""

from contextlib import asynccontextmanager
import logging
import subprocess
import os
import queue
import shutil
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    camera_daemon.stop()


app = FastAPI(
    title="Camera Service",
    description="HTTP bridge for Canon camera control",
    version="0.1.0",
    lifespan=lifespan,
)

# State
camera_state = {
    "is_recording": False,
    "is_available": False,
    "mock_mode": False,
    "last_command_elapsed_us": None,
}

# Path to C++ executables (to be compiled)
CAMERA_DIR = Path(__file__).parent
CAMERA_CONTROL_BIN = CAMERA_DIR / "CameraControl"
START_RECORD_EXE = CAMERA_DIR / "StartRecord.exe"
STOP_RECORD_EXE = CAMERA_DIR / "StopRecord.exe"
EDSDK_LIB_DIR = CAMERA_DIR / "EDSDK" / "EDSDKv132010L" / "Linux" / "EDSDK" / "Library" / "x86_64"


class CameraStatus(BaseModel):
    """Camera status response."""
    is_recording: bool
    is_available: bool
    mock_mode: bool = False
    last_command_elapsed_us: int | None = None


class CameraDaemon:
    """Long-lived EDSDK process so record start does not pay SDK startup cost."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        lib_paths = [str(CAMERA_DIR), str(EDSDK_LIB_DIR)]
        existing = env.get("LD_LIBRARY_PATH")
        if existing:
            lib_paths.append(existing)
        env["LD_LIBRARY_PATH"] = ":".join(lib_paths)
        return env

    def _drain_stdout(self):
        if not self.process or not self.process.stdout:
            return

        for line in self.process.stdout:
            self.stdout_queue.put(line.rstrip())

    def _drain_stderr(self):
        if not self.process or not self.process.stderr:
            return

        for line in self.process.stderr:
            logger.warning("CameraControl: %s", line.rstrip())

    def _release_gvfs_camera_mount(self):
        if not shutil.which("gio"):
            return

        try:
            result = subprocess.run(
                ["gio", "mount", "-l"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            return

        for line in result.stdout.splitlines():
            if "gphoto2://" not in line:
                continue

            mount_uri = line.split("->", 1)[-1].strip()
            if not mount_uri.startswith("gphoto2://"):
                continue

            subprocess.run(
                ["gio", "mount", "-u", mount_uri],
                capture_output=True,
                text=True,
                timeout=3,
            )

    def ensure_started(self) -> str:
        if not CAMERA_CONTROL_BIN.exists():
            raise FileNotFoundError(f"{CAMERA_CONTROL_BIN} not found")

        with self.lock:
            if self.process and self.process.poll() is None:
                return "OK already_ready"

            self._release_gvfs_camera_mount()
            self.process = subprocess.Popen(
                [str(CAMERA_CONTROL_BIN), "daemon"],
                cwd=str(CAMERA_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._env(),
            )
            self.stdout_queue = queue.Queue()
            self.stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
            self.stdout_thread.start()
            self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self.stderr_thread.start()

            try:
                line = self._read_line(timeout=5)
            except Exception:
                self._close_process_locked()
                raise

            if line.startswith("OK ready"):
                return line

            self._close_process_locked()
            raise RuntimeError(line)

    def is_running(self) -> bool:
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def command(self, command: str, timeout: float = 5.0) -> str:
        self.ensure_started()

        with self.lock:
            if not self.process or not self.process.stdin or not self.process.stdout:
                raise RuntimeError("Camera daemon is not running")

            self.process.stdin.write(f"{command}\n")
            self.process.stdin.flush()

            return self._read_line(timeout=timeout)

    def _read_line(self, timeout: float) -> str:
        try:
            line = self.stdout_queue.get(timeout=timeout)
        except queue.Empty as exc:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"Camera daemon exited with {self.process.returncode}") from exc
            raise TimeoutError("Timed out waiting for camera daemon output") from exc

        if not line and self.process and self.process.poll() is not None:
            raise RuntimeError(f"Camera daemon exited with {self.process.returncode}")
        return line

    def stop(self):
        with self.lock:
            self._close_process_locked()

    def _close_process_locked(self):
        process = self.process
        if process is None:
            return

        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
            except Exception:
                pass

            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        else:
            try:
                process.wait(timeout=0)
            except Exception:
                pass

        self.process = None


camera_daemon = CameraDaemon()


def parse_elapsed_us(line: str) -> int | None:
    for part in line.split():
        if part.startswith("elapsed_us="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def use_mock_camera() -> bool:
    return not CAMERA_CONTROL_BIN.exists() and not has_legacy_camera()


def has_legacy_camera() -> bool:
    return START_RECORD_EXE.exists() and STOP_RECORD_EXE.exists()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Camera Service",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/status", response_model=CameraStatus)
async def get_status():
    """Get camera status."""
    if CAMERA_CONTROL_BIN.exists():
        camera_state["is_available"] = True
        camera_state["mock_mode"] = False

        if camera_daemon.is_running():
            try:
                line = camera_daemon.command("status", timeout=2)
                if line.startswith("OK"):
                    camera_state["is_recording"] = "recording=1" in line
            except Exception as e:
                logger.warning("Camera daemon status unavailable: %s", e)
                camera_state["is_recording"] = False
        else:
            camera_state["is_recording"] = False
    elif has_legacy_camera():
        camera_state["is_available"] = True
        camera_state["mock_mode"] = False

    return camera_state


@app.post("/prepare")
async def prepare_camera():
    """Initialize EDSDK and open a camera session before the experiment clock starts."""
    if CAMERA_CONTROL_BIN.exists():
        try:
            line = camera_daemon.ensure_started()
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False
            return {"success": True, "message": line}
        except Exception as e:
            logger.error("Failed to prepare camera: %s", e)
            camera_state["is_available"] = False
            raise HTTPException(status_code=500, detail=str(e))

    if has_legacy_camera():
        camera_state["is_available"] = True
        camera_state["mock_mode"] = False
        return {"success": True, "message": "Legacy camera executables available; no prepare step"}

    logger.warning("CameraControl not found - prepare is MOCK")
    camera_state["is_available"] = True
    camera_state["mock_mode"] = True
    return {"success": True, "message": "Camera prepared (MOCK)"}


@app.post("/start_record")
async def start_record():
    """
    Start camera recording.
    
    In production: sends a command to the CameraControl EDSDK daemon.
    In development: runs in mock mode
    """
    if camera_state["is_recording"]:
        raise HTTPException(status_code=400, detail="Camera already recording")
    
    try:
        if CAMERA_CONTROL_BIN.exists():
            logger.info("Starting camera recording via EDSDK daemon...")
            line = camera_daemon.command("start")
            if not line.startswith("OK"):
                raise RuntimeError(line)

            camera_state["last_command_elapsed_us"] = parse_elapsed_us(line)
            camera_state["is_recording"] = True
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False
            logger.info("Camera recording started")
        elif START_RECORD_EXE.exists():
            logger.info("Starting camera recording via legacy executable...")
            result = subprocess.run(
                [str(START_RECORD_EXE)],
                cwd=str(CAMERA_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "StartRecord failed")
            camera_state["is_recording"] = True
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False
            camera_state["last_command_elapsed_us"] = None
            logger.info("Camera recording started")
        else:
            # Mock mode for development
            logger.warning("CameraControl not found - running in MOCK mode")
            camera_state["is_recording"] = True
            camera_state["is_available"] = True
            camera_state["mock_mode"] = True
            logger.info("Camera recording started (MOCK)")
        
        return {
            "success": True,
            "message": "Recording started",
            "is_recording": camera_state["is_recording"],
            "elapsed_us": camera_state["last_command_elapsed_us"],
        }
        
    except Exception as e:
        logger.error(f"Failed to start recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop_record")
async def stop_record():
    """
    Stop camera recording.
    
    In production: sends a command to the CameraControl EDSDK daemon.
    In development: runs in mock mode
    """
    if not camera_state["is_recording"]:
        raise HTTPException(status_code=400, detail="Camera not recording")
    
    try:
        if CAMERA_CONTROL_BIN.exists():
            logger.info("Stopping camera recording via EDSDK daemon...")
            line = camera_daemon.command("stop")
            if not line.startswith("OK"):
                raise RuntimeError(line)

            camera_state["last_command_elapsed_us"] = parse_elapsed_us(line)
            camera_state["is_recording"] = False
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False
            logger.info("Camera recording stopped")
        elif STOP_RECORD_EXE.exists():
            logger.info("Stopping camera recording via legacy executable...")
            result = subprocess.run(
                [str(STOP_RECORD_EXE)],
                cwd=str(CAMERA_DIR),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.error(f"StopRecord failed: {result.stderr}")
                raise HTTPException(status_code=500, detail="Failed to stop recording")

            camera_state["is_recording"] = False
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False
            camera_state["last_command_elapsed_us"] = None
            logger.info("Camera recording stopped")
        else:
            # Mock mode for development
            logger.warning("CameraControl not found - running in MOCK mode")
            camera_state["is_recording"] = False
            camera_state["mock_mode"] = True
            logger.info("Camera recording stopped (MOCK)")
        
        return {
            "success": True,
            "message": "Recording stopped",
            "is_recording": camera_state["is_recording"],
            "elapsed_us": camera_state["last_command_elapsed_us"],
        }
        
    except subprocess.TimeoutExpired:
        logger.error("StopRecord executable timed out")
        raise HTTPException(status_code=500, detail="Timeout stopping camera")
    except Exception as e:
        logger.error(f"Failed to stop recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/release")
async def release_camera():
    """Close the EDSDK daemon session so file-transfer tools can access the camera."""
    if camera_state["is_recording"]:
        raise HTTPException(status_code=400, detail="Stop recording before releasing the camera")

    try:
        camera_daemon.stop()
        camera_state["is_recording"] = False
        camera_state["last_command_elapsed_us"] = None

        if CAMERA_CONTROL_BIN.exists() or has_legacy_camera():
            camera_state["is_available"] = True
            camera_state["mock_mode"] = False

        return {
            "success": True,
            "message": "Camera session released",
            "is_recording": camera_state["is_recording"],
        }
    except Exception as e:
        logger.error("Failed to release camera session: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "camera_available": camera_state["is_available"],
        "mock_mode": camera_state["mock_mode"],
    }


if __name__ == "__main__":
    import uvicorn
    
    # Check if executables exist
    if CAMERA_CONTROL_BIN.exists():
        logger.info("CameraControl binary found")
    elif START_RECORD_EXE.exists() and STOP_RECORD_EXE.exists():
        logger.info("Legacy camera C++ executables found")
    else:
        logger.warning("Camera C++ executables not found - will run in MOCK mode")
        logger.info("To use real camera, run ./build_camera.sh")
    
    # Run service on port 8001
    uvicorn.run(app, host="0.0.0.0", port=8001)
