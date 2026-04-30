"""Main FastAPI application for ECA Testing Webapp."""

import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from measurement_controller import MeasurementController
from api_models import (
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    logger.info("Starting ECA Testing Webapp...")
    
    # Check camera availability
    await controller.camera.check_availability()
    
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
        session_id = await controller.start_measurement(request.config)
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
async def stop_measurement():
    """
    Stop the current measurement session.
    
    Returns:
        Session information and file paths
    """
    try:
        result = await controller.stop_measurement()
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
        await controller.camera.check_availability()
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
    
    try:
        while True:
            # Get current reading
            reading = controller.get_current_reading()
            
            # Send to client
            await websocket.send_json(reading)
            
            # Wait before next reading
            await asyncio.sleep(0.1)  # 10 Hz update rate
            
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
