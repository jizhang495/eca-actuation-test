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
        elif key == "Destination":
            parsed["destination"] = value
        elif key == "Downloaded":
            parsed["destination"] = value
        elif key == "Metadata":
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


async def _run_camera_download():
    """Run the camera transfer helper without blocking the API event loop."""
    global _camera_download_process

    try:
        command = [sys.executable, str(CAMERA_DOWNLOAD_SCRIPT)]
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
                f"Downloaded {Path(destination).name}"
                if destination
                else "Camera recording downloaded"
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
    global _camera_download_task

    if _camera_download_task and not _camera_download_task.done():
        return _camera_download_status

    if controller.is_measuring or controller.camera.is_recording:
        raise HTTPException(
            status_code=400,
            detail="Stop the measurement and camera recording before downloading.",
        )

    if not CAMERA_DOWNLOAD_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="Camera download helper script is missing.")

    _set_camera_download_status(
        is_running=True,
        started_at=_now_iso(),
        finished_at=None,
        success=None,
        message="Downloading latest camera recording",
        session_dir=None,
        camera_file=None,
        destination=None,
        metadata_path=None,
        source_size_bytes=None,
        returncode=None,
    )
    _camera_download_task = asyncio.create_task(_run_camera_download())
    return _camera_download_status


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
