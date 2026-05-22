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
        self._last_command_elapsed_us: Optional[int] = None
        self._last_timing: dict = {}

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
                if response.status_code == 200:
                    data = response.json()
                    self._is_recording = data.get("is_recording", False)
                    self._last_command_elapsed_us = data.get("last_command_elapsed_us")
                    self._last_timing = self._extract_timing(data)
                return self._is_available
        except Exception as e:
            logger.warning(f"Camera service not available: {e}")
            self._is_available = False
            return False

    async def prepare(self) -> bool:
        """
        Initialize the camera session ahead of the experiment start clock.

        This avoids paying EDSDK startup and camera discovery latency at the
        measurement start boundary.
        """
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                response = await client.post(f"{self.camera_service_url}/prepare")

                if response.status_code == 200:
                    self._is_available = True
                    logger.info("Camera prepared")
                    return True

                logger.error(f"Failed to prepare camera: {response.text}")
                self._is_available = False
                return False
        except Exception as e:
            logger.error(f"Error preparing camera: {e}")
            self._is_available = False
            return False

    async def start_recording(self) -> bool:
        """
        Start camera recording.

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{self.camera_service_url}/start_record")
                
                if response.status_code == 200:
                    data = response.json()
                    self._is_recording = True
                    self._is_available = True
                    self._last_command_elapsed_us = data.get("elapsed_us")
                    self._last_timing = self._extract_timing(data)
                    logger.info("Camera recording started")
                    return True
                else:
                    logger.error(f"Failed to start recording: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error starting camera recording: {e}")
            self._is_available = False
            self._is_recording = False
            return False

    async def stop_recording(self) -> bool:
        """
        Stop camera recording.

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{self.camera_service_url}/stop_record")
                
                if response.status_code == 200:
                    data = response.json()
                    self._is_recording = False
                    self._last_command_elapsed_us = data.get("elapsed_us")
                    self._last_timing = self._extract_timing(data)
                    logger.info("Camera recording stopped")
                    return True
                else:
                    self._is_recording = False
                    logger.warning(
                        "Failed to stop recording; assuming camera is already stopped: %s",
                        response.text,
                    )
                    return False
                    
        except Exception as e:
            self._is_recording = False
            logger.warning(
                "Error stopping camera recording; assuming camera is already stopped: %s",
                e,
            )
            return False

    def get_status(self) -> dict:
        """
        Get camera status.

        Returns:
            Dictionary with camera status information
        """
        return {
            "is_recording": self._is_recording,
            "is_available": self._is_available,
            "last_command_elapsed_us": self._last_command_elapsed_us,
            "timing": self._last_timing,
        }

    @property
    def is_recording(self) -> bool:
        """Check if camera is currently recording."""
        return self._is_recording

    @property
    def is_available(self) -> bool:
        """Check if camera service is available."""
        return self._is_available

    @property
    def last_command_elapsed_us(self) -> Optional[int]:
        """Last camera command duration reported by the camera service."""
        return self._last_command_elapsed_us

    @staticmethod
    def _extract_timing(payload: dict) -> dict:
        keys = (
            "last_request_received_epoch_us",
            "last_command_write_epoch_us",
            "last_command_flush_epoch_us",
            "last_response_epoch_us",
            "last_http_elapsed_us",
            "last_daemon_received_epoch_us",
            "last_daemon_completed_epoch_us",
            "last_daemon_line",
        )
        return {key: payload.get(key) for key in keys if key in payload}

    @property
    def last_timing(self) -> dict:
        """Last timing payload reported by the camera service."""
        return self._last_timing.copy()
