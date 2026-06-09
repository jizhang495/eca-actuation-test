#!/usr/bin/env python3
"""Run a saved experiment config through the FastAPI app HTTP API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "user-data" / "experiment-configs"
DEFAULT_APP_URL = "http://localhost:8000"
DEFAULT_CAMERA_URL = "http://localhost:8001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start a saved experiment preset through the web app HTTP API so "
            "browser clients and agent clients monitor the same run."
        )
    )
    parser.add_argument(
        "config",
        help=(
            "Saved config path under user-data/experiment-configs, e.g. "
            "step_voltage_relay2_750s_oscilloscope.json or "
            "sweeps/0_moku_first_compare_0p5v_0p1hz.json"
        ),
    )
    parser.add_argument("--app-url", default=DEFAULT_APP_URL)
    parser.add_argument("--camera-url", default=DEFAULT_CAMERA_URL)
    parser.add_argument(
        "--no-start-services",
        action="store_true",
        help="Fail instead of starting missing app/camera HTTP services.",
    )
    parser.add_argument(
        "--leave-services-running",
        action="store_true",
        help="Do not terminate services started by this script after the run.",
    )
    parser.add_argument(
        "--download-camera",
        action="store_true",
        help="After the run, call the app endpoint that downloads the newest camera movie.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Status polling interval. Defaults to 5 seconds.",
    )
    parser.add_argument(
        "--progress-seconds",
        type=float,
        default=30.0,
        help="Minimum interval between progress lines. Defaults to 30 seconds.",
    )
    return parser.parse_args()


def config_file_name(value: str) -> str:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Config path must be a relative path under user-data/experiment-configs")
    if path.suffix.lower() != ".json":
        path = path.with_name(f"{path.name}.json")
    return path.as_posix()


def load_local_config(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def request_json(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def service_available(url: str, path: str = "/health") -> bool:
    try:
        request_json("GET", f"{url.rstrip('/')}{path}", timeout=3.0)
        return True
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False


def start_service(command: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_for_service(url: str, path: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_available(url, path):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Service did not become ready: {url}{path}")


def maybe_start_app(app_url: str, no_start: bool) -> subprocess.Popen | None:
    if service_available(app_url, "/health"):
        return None
    if no_start:
        raise RuntimeError(f"App service is not running: {app_url}")

    process = start_service(
        [
            "uv",
            "run",
            "uvicorn",
            "app:app",
            "--app-dir",
            "eca-actuation-test",
            "--host",
            "0.0.0.0",
            "--port",
            app_url.rsplit(":", 1)[-1],
        ],
        REPO_ROOT,
    )
    wait_for_service(app_url, "/health", timeout=30.0)
    return process


def maybe_start_camera(camera_url: str, no_start: bool, record_camera: bool) -> subprocess.Popen | None:
    if not record_camera:
        return None
    if service_available(camera_url, "/health"):
        return None
    if no_start:
        raise RuntimeError(f"Camera service is not running: {camera_url}")

    process = start_service(
        ["uv", "run", "python3", "camera/camera_service.py"],
        REPO_ROOT,
    )
    wait_for_service(camera_url, "/health", timeout=30.0)
    return process


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_measurement(app_url: str, session_id: str, poll_seconds: float, progress_seconds: float) -> None:
    last_progress = -progress_seconds
    while True:
        status = request_json("GET", f"{app_url.rstrip('/')}/api/status", timeout=10.0)
        elapsed = status.get("elapsed_time") or 0.0
        if elapsed - last_progress >= progress_seconds or elapsed == 0.0:
            print(
                f"measuring {session_id} elapsed={elapsed:.1f}s "
                f"camera_recording={status.get('camera_recording')}",
                flush=True,
            )
            last_progress = elapsed

        if not status.get("is_measuring"):
            return
        time.sleep(max(0.1, poll_seconds))


def download_camera(app_url: str, poll_seconds: float) -> None:
    request_json("POST", f"{app_url.rstrip('/')}/api/download_latest_camera_recording", timeout=10.0)
    while True:
        status = request_json(
            "GET",
            f"{app_url.rstrip('/')}/api/download_latest_camera_recording/status",
            timeout=10.0,
        )
        if not status.get("is_running"):
            if not status.get("success"):
                raise RuntimeError(status.get("message") or "Camera download failed")
            print(status.get("message", "Camera recording downloaded"), flush=True)
            return
        time.sleep(max(1.0, poll_seconds))


def main() -> int:
    args = parse_args()
    app_url = args.app_url.rstrip("/")
    camera_url = args.camera_url.rstrip("/")
    name = config_file_name(args.config)
    config = load_local_config(name)

    app_process = None
    camera_process = None
    try:
        camera_process = maybe_start_camera(
            camera_url,
            args.no_start_services,
            bool(config.get("record_camera")),
        )
        app_process = maybe_start_app(app_url, args.no_start_services)

        query = urlencode({"control_source": "agent"})
        start_url = f"{app_url}/api/experiment_configs/start/{quote(name, safe='/')}?{query}"
        response = request_json("POST", start_url, timeout=30.0)
        session_id = response["session_id"]
        print(f"started {session_id} from {name}", flush=True)

        wait_for_measurement(
            app_url,
            session_id,
            poll_seconds=args.poll_seconds,
            progress_seconds=args.progress_seconds,
        )
        print(f"measurement stopped {session_id}", flush=True)

        if args.download_camera and config.get("record_camera"):
            download_camera(app_url, args.poll_seconds)

        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.leave_services_running:
            terminate_process(app_process)
            terminate_process(camera_process)


if __name__ == "__main__":
    raise SystemExit(main())
