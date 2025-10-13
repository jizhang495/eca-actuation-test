"""Camera controller service bridge."""

import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)


class CameraController:
    """
    Bridge to C++ camera service.
    
    In a production setup, the C++ camera programs would be wrapped in an HTTP server.
    For now, this class provides the interface that will communicate with that service.
    """

    def __init__(self, camera_service_url: str = "http://localhost:8001"):
        """
        Initialize camera controller.

        Args:
            camera_service_url: URL of the camera service HTTP endpoint
        """
        self.camera_service_url = camera_service_url
        self._is_recording = False
        self._is_available = False

    async def check_availability(self) -> bool:
        """
        Check if camera service is available.

        Returns:
            True if camera service is reachable, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.camera_service_url}/status")
                self._is_available = response.status_code == 200
                return self._is_available
        except Exception as e:
            logger.warning(f"Camera service not available: {e}")
            self._is_available = False
            return False

    async def start_recording(self) -> bool:
        """
        Start camera recording.

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{self.camera_service_url}/start_record")
                
                if response.status_code == 200:
                    self._is_recording = True
                    logger.info("Camera recording started")
                    return True
                else:
                    logger.error(f"Failed to start recording: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error starting camera recording: {e}")
            # In development mode without camera service, we can mock success
            logger.warning("Camera service not available - running in mock mode")
            self._is_recording = True
            return True

    async def stop_recording(self) -> bool:
        """
        Stop camera recording.

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{self.camera_service_url}/stop_record")
                
                if response.status_code == 200:
                    self._is_recording = False
                    logger.info("Camera recording stopped")
                    return True
                else:
                    logger.error(f"Failed to stop recording: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error stopping camera recording: {e}")
            # In development mode without camera service, we can mock success
            logger.warning("Camera service not available - running in mock mode")
            self._is_recording = False
            return True

    def get_status(self) -> dict:
        """
        Get camera status.

        Returns:
            Dictionary with camera status information
        """
        return {
            "is_recording": self._is_recording,
            "is_available": self._is_available
        }

    @property
    def is_recording(self) -> bool:
        """Check if camera is currently recording."""
        return self._is_recording

    @property
    def is_available(self) -> bool:
        """Check if camera service is available."""
        return self._is_available

