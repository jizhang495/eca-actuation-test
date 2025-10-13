"""
Camera Service HTTP Bridge

This service provides HTTP endpoints to control the Canon camera
via the C++ executables (StartRecord and StopRecord).

In production, you would compile the C++ code and this service would
call those executables. For development, it provides a mock mode.
"""

import logging
import subprocess
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Camera Service",
    description="HTTP bridge for Canon camera control",
    version="0.1.0"
)

# State
camera_state = {
    "is_recording": False,
    "is_available": True  # Set based on whether camera is connected
}

# Path to C++ executables (to be compiled)
CAMERA_DIR = Path(__file__).parent
START_RECORD_EXE = CAMERA_DIR / "StartRecord.exe"
STOP_RECORD_EXE = CAMERA_DIR / "StopRecord.exe"


class CameraStatus(BaseModel):
    """Camera status response."""
    is_recording: bool
    is_available: bool


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
    return camera_state


@app.post("/start_record")
async def start_record():
    """
    Start camera recording.
    
    In production: calls StartRecord.exe
    In development: runs in mock mode
    """
    if camera_state["is_recording"]:
        raise HTTPException(status_code=400, detail="Camera already recording")
    
    try:
        # Check if C++ executable exists
        if START_RECORD_EXE.exists():
            # Call the executable
            logger.info("Starting camera recording via C++ executable...")
            process = subprocess.Popen(
                [str(START_RECORD_EXE)],
                cwd=str(CAMERA_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Note: The C++ program should be modified to not wait indefinitely
            # For now, we'll start it as a background process
            
            camera_state["is_recording"] = True
            logger.info("Camera recording started")
            
        else:
            # Mock mode for development
            logger.warning("StartRecord.exe not found - running in MOCK mode")
            camera_state["is_recording"] = True
            logger.info("Camera recording started (MOCK)")
        
        return {
            "success": True,
            "message": "Recording started",
            "is_recording": camera_state["is_recording"]
        }
        
    except Exception as e:
        logger.error(f"Failed to start recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop_record")
async def stop_record():
    """
    Stop camera recording.
    
    In production: calls StopRecord.exe
    In development: runs in mock mode
    """
    if not camera_state["is_recording"]:
        raise HTTPException(status_code=400, detail="Camera not recording")
    
    try:
        # Check if C++ executable exists
        if STOP_RECORD_EXE.exists():
            # Call the executable
            logger.info("Stopping camera recording via C++ executable...")
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
            logger.info("Camera recording stopped")
            
        else:
            # Mock mode for development
            logger.warning("StopRecord.exe not found - running in MOCK mode")
            camera_state["is_recording"] = False
            logger.info("Camera recording stopped (MOCK)")
        
        return {
            "success": True,
            "message": "Recording stopped",
            "is_recording": camera_state["is_recording"]
        }
        
    except subprocess.TimeoutExpired:
        logger.error("StopRecord executable timed out")
        raise HTTPException(status_code=500, detail="Timeout stopping camera")
    except Exception as e:
        logger.error(f"Failed to stop recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "camera_available": camera_state["is_available"]
    }


if __name__ == "__main__":
    import uvicorn
    
    # Check if executables exist
    if START_RECORD_EXE.exists() and STOP_RECORD_EXE.exists():
        logger.info("Camera C++ executables found")
    else:
        logger.warning("Camera C++ executables not found - will run in MOCK mode")
        logger.info("To use real camera, compile StartRecord.cpp and StopRecord.cpp")
    
    # Run service on port 8001
    uvicorn.run(app, host="0.0.0.0", port=8001)

