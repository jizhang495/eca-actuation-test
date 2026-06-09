#!/usr/bin/env python3
"""Record the newest camera movie name in a session without downloading it."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from download_latest_camera_recording import (
    DEFAULT_EXTENSIONS,
    REPO_ROOT,
    discover_gio_camera_uri,
    find_latest_movie_gio,
    latest_session_dir,
    release_camera_service,
    resolve_sessions_root,
)


def append_session_log(session_dir: Path, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (session_dir / "log.txt").open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find the newest movie on the camera and write a session-local "
            "reference file for later manual SD-card copy."
        )
    )
    parser.add_argument("--sessions-root", help="Directory containing session folders.")
    parser.add_argument("--session-dir", help="Specific session directory to annotate.")
    parser.add_argument("--camera-uri", help="Explicit gphoto2:// camera URI.")
    parser.add_argument(
        "--extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="Comma-separated movie extensions to consider.",
    )
    parser.add_argument(
        "--reference-name",
        default="camera_recording_reference.json",
        help="Session-local JSON file name. Defaults to camera_recording_reference.json.",
    )
    parser.add_argument(
        "--keep-camera-service-session",
        action="store_true",
        help="Do not call camera service /release before querying camera storage.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print only; do not write files.")
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

    release_camera_service(skip_release=args.keep_camera_service_session)
    camera_uri = discover_gio_camera_uri(args.camera_uri)
    latest_movie = find_latest_movie_gio(camera_uri, extensions)

    reference_path = session_dir / args.reference_name
    payload = {
        "referenced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_dir": str(session_dir),
        "source_uri": latest_movie.uri,
        "source_name": latest_movie.name,
        "source_modified_unix": latest_movie.modified,
        "source_size_bytes": latest_movie.size,
        "manual_copy_note": (
            "This movie was not downloaded automatically. Copy the named file "
            "from the camera SD card into this session folder when needed."
        ),
    }

    print(f"Session: {session_dir}")
    print(f"Camera file: {latest_movie.uri}")
    print(f"Camera file name: {latest_movie.name}")
    if latest_movie.size is not None:
        print(f"Size: {latest_movie.size} bytes")
    print(f"Reference metadata: {reference_path}")
    print(f"Log: {session_dir / 'log.txt'}")

    if args.dry_run:
        print("Dry run only; no reference written.")
        return 0

    reference_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    append_session_log(
        session_dir,
        "Camera recording reference: "
        f"{latest_movie.name}; source {latest_movie.uri}; "
        f"metadata {reference_path.name}",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
