#!/usr/bin/env python3
"""Download the newest camera movie and create a session-local MP4."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSIONS_DIR = REPO_ROOT / "user-data" / "sessions"
DEFAULT_BIG_VIDEOS_DIR = REPO_ROOT / "user-data" / "big-videos"
DEFAULT_CAMERA_SERVICE_URL = "http://localhost:8001"
DEFAULT_EXTENSIONS = (".mov", ".mp4", ".m4v", ".avi")
DEFAULT_CRF = 22
DEFAULT_PRESET = "medium"


@dataclass(frozen=True)
class CameraFile:
    uri: str
    name: str
    modified: int
    size: int | None


def resolve_sessions_root(value: str | None) -> Path:
    configured = value or os.getenv("ECA_DATA_DIR")
    if not configured:
        return DEFAULT_SESSIONS_DIR

    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def resolve_big_videos_dir(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_BIG_VIDEOS_DIR
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def latest_session_dir(sessions_root: Path) -> Path:
    if not sessions_root.exists():
        raise RuntimeError(f"Sessions directory does not exist: {sessions_root}")

    sessions = [path for path in sessions_root.iterdir() if path.is_dir()]
    if not sessions:
        raise RuntimeError(f"No session directories found in {sessions_root}")

    return max(sessions, key=lambda path: path.stat().st_mtime)


def run_command(command: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def request_camera_service(path: str, method: str = "GET", timeout: float = 3.0) -> dict | None:
    request = Request(f"{DEFAULT_CAMERA_SERVICE_URL}{path}", method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return None


def release_camera_service(skip_release: bool) -> None:
    if skip_release:
        return

    status = request_camera_service("/status")
    if status and status.get("is_recording"):
        raise RuntimeError("Camera is still recording. Stop the measurement/recording before download.")

    request_camera_service("/release", method="POST")
    time.sleep(1.0)


def discover_gio_camera_uri(explicit_uri: str | None) -> str:
    if explicit_uri:
        return explicit_uri.rstrip("/")

    if not shutil.which("gio"):
        raise RuntimeError("gio is not installed and gphoto2 is unavailable")

    result = run_command(["gio", "mount", "-l"], timeout=5)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gio mount -l failed")

    for line in result.stdout.splitlines():
        match = re.search(r"(gphoto2://\S+)", line)
        if match:
            return match.group(1).rstrip("/")

    result = run_command(["gio", "mount", "-li"], timeout=5)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gio mount -li failed")

    for line in result.stdout.splitlines():
        match = re.search(r"activation_root=(gphoto2://\S+)", line)
        if not match:
            continue

        camera_uri = match.group(1).rstrip("/")
        mount_result = run_command(["gio", "mount", f"{camera_uri}/"], timeout=30)
        if mount_result.returncode == 0:
            return camera_uri

        message = mount_result.stderr.strip() or f"gio mount failed for {camera_uri}"
        raise RuntimeError(message)

    raise RuntimeError("No gphoto2 camera volume found. Check that the camera is powered on and connected.")


def gio_list(uri: str) -> list[str]:
    result = run_command(
        ["gio", "list", "-u", "-a", "standard::type,standard::size,time::modified", uri],
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gio list failed for {uri}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def parse_gio_file_line(line: str) -> CameraFile | None:
    parts = line.split("\t")
    if not parts:
        return None

    uri = parts[0].strip()
    if "(regular)" not in line:
        return None

    size: int | None = None
    if len(parts) >= 2:
        try:
            size = int(parts[1].strip())
        except ValueError:
            size = None

    modified = 0
    match = re.search(r"time::modified=(\d+)", line)
    if match:
        modified = int(match.group(1))

    return CameraFile(uri=uri, name=Path(uri).name, modified=modified, size=size)


def find_latest_movie_gio(root_uri: str, extensions: tuple[str, ...]) -> CameraFile:
    stack = [root_uri]
    movies: list[CameraFile] = []

    while stack:
        uri = stack.pop()
        for line in gio_list(uri):
            child_uri = line.split("\t", 1)[0].strip()
            if "(directory)" in line:
                stack.append(child_uri.rstrip("/"))
                continue

            camera_file = parse_gio_file_line(line)
            if camera_file and camera_file.name.lower().endswith(extensions):
                movies.append(camera_file)

    if not movies:
        extension_list = ", ".join(extensions)
        raise RuntimeError(f"No movie files found on camera matching: {extension_list}")

    return max(movies, key=lambda item: (item.modified, item.name))


def unique_destination(path: Path, force: bool) -> Path:
    if force or not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not choose a unique destination for {path}")


def copy_gio_file(source_uri: str, destination: Path, force: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if force:
        destination.unlink(missing_ok=True)

    command = ["gio", "copy", "--progress"]
    command.extend([source_uri, str(destination)])

    result = run_command(command, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gio copy failed for {source_uri}")


def write_metadata(destination: Path, session_dir: Path, camera_file: CameraFile) -> None:
    metadata_path = destination.with_suffix(destination.suffix + ".json")
    payload = {
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_dir": str(session_dir),
        "source_uri": camera_file.uri,
        "source_name": camera_file.name,
        "source_modified_unix": camera_file.modified,
        "source_size_bytes": camera_file.size,
        "local_file": str(destination),
    }
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n")


def find_ffmpeg(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg was not found on PATH. Install ffmpeg, pass --ffmpeg-bin, "
            "or install the Python fallback with: python3 -m pip install imageio-ffmpeg"
        ) from exc

    return imageio_ffmpeg.get_ffmpeg_exe()


def convert_mov_to_mp4(
    source: Path,
    destination: Path,
    crf: int,
    preset: str,
    ffmpeg_bin: str | None,
    force: bool,
) -> None:
    if destination.exists() and not force:
        raise FileExistsError(f"Output already exists: {destination}. Use --force to replace it.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        find_ffmpeg(ffmpeg_bin),
        "-hide_banner",
        "-nostdin",
        "-y" if force else "-n",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download the newest camera movie into user-data/big-videos and "
            "create an MP4 in the newest user-data session folder."
        )
    )
    parser.add_argument("--sessions-root", help="Directory containing session folders.")
    parser.add_argument("--session-dir", help="Specific session directory to download into.")
    parser.add_argument(
        "--big-videos-dir",
        help="Directory for raw camera movies. Defaults to user-data/big-videos.",
    )
    parser.add_argument("--camera-uri", help="Explicit gphoto2:// camera URI.")
    parser.add_argument(
        "--output-name",
        help="Raw video filename under big-videos. Defaults to the original camera filename.",
    )
    parser.add_argument(
        "--mp4-output-name",
        help="Session-local MP4 filename. Defaults to raw video stem with .mp4.",
    )
    parser.add_argument(
        "--extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="Comma-separated movie extensions to consider.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite the destination file.")
    parser.add_argument("--crf", type=int, default=DEFAULT_CRF, help="H.264 CRF for MP4 conversion.")
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help="libx264 preset for MP4 conversion. Defaults to medium.",
    )
    parser.add_argument("--ffmpeg-bin", help="Path to an ffmpeg executable.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied.")
    parser.add_argument(
        "--keep-camera-service-session",
        action="store_true",
        help="Do not call camera service /release before transfer.",
    )
    args = parser.parse_args()

    extensions = tuple(
        extension.strip().lower() if extension.strip().startswith(".") else f".{extension.strip().lower()}"
        for extension in args.extensions.split(",")
        if extension.strip()
    )

    session_dir = Path(args.session_dir).expanduser() if args.session_dir else latest_session_dir(
        resolve_sessions_root(args.sessions_root)
    )
    if not session_dir.is_absolute():
        session_dir = (REPO_ROOT / session_dir).resolve()
    if not session_dir.exists() or not session_dir.is_dir():
        raise RuntimeError(f"Session directory does not exist: {session_dir}")
    big_videos_dir = resolve_big_videos_dir(args.big_videos_dir)

    release_camera_service(skip_release=args.keep_camera_service_session)
    camera_uri = discover_gio_camera_uri(args.camera_uri)
    latest_movie = find_latest_movie_gio(camera_uri, extensions)
    output_name = args.output_name or latest_movie.name
    raw_destination = unique_destination(big_videos_dir / output_name, args.force)
    mp4_destination_name = args.mp4_output_name or f"{Path(output_name).stem}.mp4"
    mp4_destination = unique_destination(session_dir / mp4_destination_name, args.force)

    print(f"Session: {session_dir}")
    print(f"Camera file: {latest_movie.uri}")
    if latest_movie.size is not None:
        print(f"Size: {latest_movie.size} bytes")
    print(f"Raw destination: {raw_destination}")
    print(f"MP4 destination: {mp4_destination}")

    if args.dry_run:
        print("Dry run only; no file copied.")
        return 0

    copy_gio_file(latest_movie.uri, raw_destination, force=args.force)
    write_metadata(raw_destination, session_dir, latest_movie)
    print(f"Downloaded raw: {raw_destination}")
    print(f"Raw metadata: {raw_destination.with_suffix(raw_destination.suffix + '.json')}")

    if raw_destination.suffix.lower() == ".mp4":
        shutil.copy2(raw_destination, mp4_destination)
    else:
        convert_mov_to_mp4(
            source=raw_destination,
            destination=mp4_destination,
            crf=args.crf,
            preset=args.preset,
            ffmpeg_bin=args.ffmpeg_bin,
            force=args.force,
        )
    print(f"Converted MP4: {mp4_destination}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
