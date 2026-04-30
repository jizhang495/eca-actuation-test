"""Main FastAPI application for ECA Testing Webapp."""

import logging
import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from measurement_controller import MeasurementController
from api_models import (
    ControlSource,
    StartMeasurementRequest,
    StopMeasurementResponse,
    SystemStatus,
    InstrumentListResponse,
    DMMReading,
    SessionInfo
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
