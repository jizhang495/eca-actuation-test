"""Main FastAPI application for ECA Testing Webapp."""

import logging
import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from measurement_controller import MeasurementController
from api_models import (
    ControlSource,
    MeasurementConfig,
    SaveExperimentConfigRequest,
    SaveExperimentConfigResponse,
    StartMeasurementRequest,
    StopMeasurementResponse,
    SystemStatus,
    InstrumentListResponse,
    DMMReading,
    SessionInfo,
    CameraDownloadStatus,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global measurement controller
controller = MeasurementController()
_last_camera_status_check = 0.0
REPO_ROOT = Path(__file__).resolve().parents[1]
CAMERA_DOWNLOAD_SCRIPT = REPO_ROOT / "scripts" / "download_latest_camera_recording.py"
EXPERIMENT_CONFIG_DIR = REPO_ROOT / "user-data" / "experiment-configs"
_camera_download_task: asyncio.Task | None = None
_camera_download_process: asyncio.subprocess.Process | None = None
_camera_download_status = CameraDownloadStatus().model_dump()
_auto_download_watch_task: asyncio.Task | None = None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _set_camera_download_status(**updates):
    _camera_download_status.update(updates)


def _parse_camera_download_output(stdout: str) -> dict:
    """Extract stable fields from the camera download helper output."""
    parsed: dict = {}

    for line in stdout.splitlines():
        key, separator, value = line.partition(": ")
        if not separator:
            continue

        if key == "Session":
            parsed["session_dir"] = value
        elif key == "Camera file":
            parsed["camera_file"] = value
        elif key in {"Destination", "MP4 destination", "Converted MP4"}:
            parsed["destination"] = value
        elif key == "Raw destination":
            parsed["raw_destination"] = value
        elif key in {"Downloaded", "Downloaded raw"}:
            parsed["raw_destination"] = value
        elif key == "Metadata":
            parsed["metadata_path"] = value
        elif key == "Raw metadata":
            parsed["raw_metadata_path"] = value
            parsed["metadata_path"] = value
        elif key == "Size":
            size_text = value.removesuffix(" bytes")
            if size_text.isdigit():
                parsed["source_size_bytes"] = int(size_text)

    return parsed


def _config_file_name(requested_name: str | None, test_name: str) -> str:
    """Return a conservative JSON filename for a saved experiment config."""
    base_name = requested_name or test_name or "experiment_config"
    base_name = Path(base_name).name
    if base_name.lower().endswith(".json"):
        base_name = base_name[:-5]

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("._")
    if not safe_name:
        safe_name = "experiment_config"

    return f"{safe_name}.json"


def _experiment_config_path(file_name: str) -> Path:
    """Resolve a saved experiment config filename without allowing traversal."""
    safe_name = Path(file_name).name
    if not safe_name.lower().endswith(".json"):
        safe_name = f"{safe_name}.json"
    if safe_name in {"", ".", ".."}:
        raise ValueError("Experiment config filename is required")

    config_path = EXPERIMENT_CONFIG_DIR / safe_name
    resolved_root = EXPERIMENT_CONFIG_DIR.resolve()
    resolved_path = config_path.resolve()
    if resolved_root not in resolved_path.parents and resolved_path != resolved_root:
        raise ValueError("Invalid experiment config filename")
    return resolved_path


def _should_auto_download_camera(config: MeasurementConfig) -> bool:
    return bool(config.record_camera and config.auto_download_camera_recording)


def _start_camera_download_task(
    session_dir: str | Path | None = None,
    message: str = "Downloading raw camera recording and converting to MP4",
) -> dict:
    """Start the camera transfer helper and expose progress through status."""
    global _camera_download_task

    if _camera_download_task and not _camera_download_task.done():
        return _camera_download_status

    if not CAMERA_DOWNLOAD_SCRIPT.exists():
        raise RuntimeError("Camera download helper script is missing.")

    session_dir_text = str(session_dir) if session_dir is not None else None
    _set_camera_download_status(
        is_running=True,
        started_at=_now_iso(),
        finished_at=None,
        success=None,
        message=message,
        session_dir=session_dir_text,
        camera_file=None,
        raw_destination=None,
        destination=None,
        raw_metadata_path=None,
        metadata_path=None,
        source_size_bytes=None,
        returncode=None,
    )
    _camera_download_task = asyncio.create_task(_run_camera_download(session_dir=session_dir))
    return _camera_download_status


async def _run_camera_download(session_dir: str | Path | None = None):
    """Run the camera transfer helper without blocking the API event loop."""
    global _camera_download_process

    try:
        command = [sys.executable, str(CAMERA_DOWNLOAD_SCRIPT)]
        if session_dir is not None:
            command.extend(["--session-dir", str(session_dir)])
        env = os.environ.copy()
        _camera_download_process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await _camera_download_process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        returncode = _camera_download_process.returncode

        parsed = _parse_camera_download_output(stdout)
        if returncode == 0:
            destination = parsed.get("destination")
            message = (
                f"Downloaded raw video and converted {Path(destination).name}"
                if destination
                else "Camera recording downloaded and converted"
            )
            _set_camera_download_status(
                is_running=False,
                finished_at=_now_iso(),
                success=True,
                message=message,
                returncode=returncode,
                **parsed,
            )
        else:
            detail = stderr.strip() or stdout.strip() or "Camera download failed"
            _set_camera_download_status(
                is_running=False,
                finished_at=_now_iso(),
                success=False,
                message=detail,
                returncode=returncode,
                **parsed,
            )
    except asyncio.CancelledError:
        if _camera_download_process and _camera_download_process.returncode is None:
            _camera_download_process.terminate()
        _set_camera_download_status(
            is_running=False,
            finished_at=_now_iso(),
            success=False,
            message="Camera download cancelled",
        )
        raise
    except Exception as exc:
        logger.error("Camera download failed: %s", exc)
        _set_camera_download_status(
            is_running=False,
            finished_at=_now_iso(),
            success=False,
            message=str(exc),
        )
    finally:
        _camera_download_process = None


async def _auto_download_after_measurement(session_id: str, session_dir: Path):
    """Wait for a session to finish, then download/convert its camera recording."""
    try:
        while controller.current_session_id == session_id:
            await asyncio.sleep(1.0)

        await asyncio.sleep(0.5)
        if controller.current_session_id is not None or controller.is_measuring:
            logger.warning(
                "Skipping auto camera download for %s because another measurement is active",
                session_id,
            )
            return

        if controller.camera.is_recording:
            logger.warning(
                "Skipping auto camera download for %s because the camera is still recording",
                session_id,
            )
            return

        _start_camera_download_task(
            session_dir=session_dir,
            message="Auto-downloading raw camera recording and converting to MP4",
        )
        logger.info("Auto camera download started for session %s", session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Auto camera download failed to start for %s: %s", session_id, exc)


def _arm_auto_camera_download(session_id: str, config: MeasurementConfig):
    """Arm the post-run camera transfer if the measurement config asks for it."""
    global _auto_download_watch_task

    if _auto_download_watch_task and not _auto_download_watch_task.done():
        _auto_download_watch_task.cancel()

    if not _should_auto_download_camera(config):
        return

    session_dir = controller.data_logger.base_data_dir / session_id
    _auto_download_watch_task = asyncio.create_task(
        _auto_download_after_measurement(session_id, session_dir)
    )


async def refresh_camera_availability(force: bool = False):
    """Refresh camera availability without making every status poll hit the camera service."""
    global _last_camera_status_check
    now = time.monotonic()
    if force or now - _last_camera_status_check >= 5.0:
        await controller.camera.check_availability()
        _last_camera_status_check = now


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    logger.info("Starting ECA Testing Webapp...")
    
    # Check camera availability
    await refresh_camera_availability(force=True)
    
    yield
    
    # Cleanup
    logger.info("Shutting down...")
    if _auto_download_watch_task and not _auto_download_watch_task.done():
        _auto_download_watch_task.cancel()
        try:
            await _auto_download_watch_task
        except asyncio.CancelledError:
            pass

    if _camera_download_task and not _camera_download_task.done():
        _camera_download_task.cancel()
        try:
            await _camera_download_task
        except asyncio.CancelledError:
            pass

    if controller.is_measuring:
        try:
            await controller.stop_measurement()
        except:
            pass


# Create FastAPI app
app = FastAPI(
    title="ECA Testing Webapp",
    description="Electrochemical Actuator Testing and Control System",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# REST API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "ECA Testing Webapp",
        "version": "0.1.0",
        "status": "running"
    }


@app.post("/api/start_measurement")
async def start_measurement(request: StartMeasurementRequest):
    """
    Start a new measurement session.
    
    Args:
        request: Measurement configuration
        
    Returns:
        Session ID and status
    """
    try:
        session_id = await controller.start_measurement(
            request.config,
            control_source=request.control_source,
        )
        _arm_auto_camera_download(session_id, request.config)
        return {
            "success": True,
            "session_id": session_id,
            "message": "Measurement started"
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting measurement: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/experiment_configs/save", response_model=SaveExperimentConfigResponse)
async def save_experiment_config(request: SaveExperimentConfigRequest):
    """
    Save an experiment configuration preset under user-data/experiment-configs.
    """
    try:
        EXPERIMENT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        file_name = _config_file_name(request.file_name, request.config.test_name)
        config_path = EXPERIMENT_CONFIG_DIR / file_name
        config_path.write_text(
            json.dumps(request.config.model_dump(), indent=2) + "\n",
            encoding="utf-8",
        )

        return {
            "success": True,
            "file_name": file_name,
            "path": str(config_path),
            "message": f"Saved {file_name}",
        }
    except Exception as e:
        logger.error(f"Error saving experiment config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/experiment_configs/start/{file_name}")
async def start_experiment_config(
    file_name: str,
    control_source: ControlSource = Query(default="api"),
):
    """
    Start a saved experiment configuration by filename.

    This is the API equivalent of selecting a preset in the browser and clicking
    Run: the saved JSON is loaded unchanged and submitted to the shared app
    controller, so browser and agent clients observe the same live run.
    """
    try:
        config_path = _experiment_config_path(file_name)
        if not config_path.exists() or not config_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"Experiment config not found: {config_path.name}",
            )

        config = MeasurementConfig.model_validate_json(
            config_path.read_text(encoding="utf-8")
        )
        session_id = await controller.start_measurement(
            config,
            control_source=control_source,
        )
        _arm_auto_camera_download(session_id, config)
        return {
            "success": True,
            "session_id": session_id,
            "file_name": config_path.name,
            "path": str(config_path),
            "message": f"Measurement started from {config_path.name}",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting experiment config {file_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stop_measurement", response_model=StopMeasurementResponse)
async def stop_measurement(control_source: ControlSource = Query(default="api")):
    """
    Stop the current measurement session.
    
    Returns:
        Session information and file paths
    """
    try:
        result = await controller.stop_measurement(control_source=control_source)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error stopping measurement: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status", response_model=SystemStatus)
async def get_status():
    """
    Get current system status.
    
    Returns:
        System status including instrument connections and measurement state
    """
    try:
        await refresh_camera_availability()
        status = controller.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/list_instruments", response_model=InstrumentListResponse)
async def list_instruments():
    """
    List all available instruments.
    
    Returns:
        Lists of VISA resources and serial ports
    """
    try:
        instruments = controller.list_available_instruments()
        return instruments
    except Exception as e:
        logger.error(f"Error listing instruments: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions")
async def list_sessions():
    """
    List all measurement sessions.
    
    Returns:
        List of session IDs
    """
    try:
        sessions = controller.data_logger.list_sessions()
        return {"sessions": sessions}
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/session/{session_id}", response_model=SessionInfo)
async def get_session_info(session_id: str):
    """
    Get information about a specific session.
    
    Args:
        session_id: Session ID to query
        
    Returns:
        Session information including files and configuration
    """
    try:
        info = controller.data_logger.get_session_info(session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")
        return info
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/session/{session_id}/data")
async def get_session_data(session_id: str):
    """
    Get data from a specific session.
    
    Args:
        session_id: Session ID to query
        
    Returns:
        Session data as JSON
    """
    try:
        info = controller.data_logger.get_session_info(session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Read CSV data
        import pandas as pd
        csv_path = f"{info['path']}/readings.csv"
        try:
            df = pd.read_csv(csv_path)
            df = df.astype(object).where(pd.notnull(df), None)
            data = df.to_dict(orient='records')
            return {"data": data}
        except FileNotFoundError:
            return {"data": []}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/current_session/data")
async def get_current_session_data(limit: int = Query(default=6000, ge=0, le=50000)):
    """
    Return recent in-memory data for the active session.

    This endpoint lets a browser opened mid-run backfill plots for runs that
    were started by API agents or external scripts.
    """
    try:
        return {
            "session_id": controller.current_session_id,
            "data": controller.get_current_session_data(limit=limit),
        }
    except Exception as e:
        logger.error(f"Error getting current session data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download_latest_camera_recording", response_model=CameraDownloadStatus)
async def download_latest_camera_recording():
    """
    Start downloading the newest camera movie into the newest measurement session.
    """
    if controller.is_measuring or controller.camera.is_recording:
        raise HTTPException(
            status_code=400,
            detail="Stop the measurement and camera recording before downloading.",
        )

    try:
        return _start_camera_download_task()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/download_latest_camera_recording/status",
    response_model=CameraDownloadStatus,
)
async def get_camera_download_status():
    """Return current or most recent latest-camera-recording download status."""
    return _camera_download_status


# ============================================================================
# WebSocket Endpoint for Real-time Data Streaming
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to websocket: {e}")


manager = ConnectionManager()


@app.websocket("/api/live")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time data streaming.
    
    Streams DMM readings to connected clients.
    """
    await manager.connect(websocket)
    last_sample_index = None
    
    try:
        while True:
            if controller.is_measuring:
                reading = controller.get_current_reading()
                sample_index = reading.get("sample_index")

                if sample_index is not None and sample_index != last_sample_index:
                    await websocket.send_json(reading)
                    last_sample_index = sample_index

            await asyncio.sleep(0.05)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "is_measuring": controller.is_measuring,
        "camera_available": controller.camera.is_available
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
