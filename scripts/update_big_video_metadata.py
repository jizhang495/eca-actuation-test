#!/usr/bin/env python3
"""Create or refresh sidecar metadata for raw camera videos."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from download_latest_camera_recording import REPO_ROOT, probe_video_metadata


DEFAULT_BIG_VIDEOS_DIR = REPO_ROOT / "user-data" / "big-videos"
DEFAULT_SOURCE_CARD_DIR = Path("/media/jz/EOS_DIGITAL/DCIM/100CANON")


def local_timestamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(seconds))


def load_sidecar(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_base_metadata(video_path: Path, source_card_dir: Path) -> dict:
    source_card_path = source_card_dir / video_path.name
    payload = {
        "source_name": video_path.name,
        "source_card_path": str(source_card_path),
        "size_bytes": video_path.stat().st_size,
        "copied_at": local_timestamp(video_path.stat().st_mtime),
        "copy_time_source": "archive_file_mtime",
        "metadata_recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sessions": [],
        "match_status": "unmatched",
    }
    return payload


def update_metadata(video_path: Path, source_card_dir: Path) -> bool:
    sidecar_path = video_path.with_suffix(video_path.suffix + ".json")
    existed = sidecar_path.exists()
    payload = load_sidecar(sidecar_path) if existed else build_base_metadata(video_path, source_card_dir)

    payload.setdefault("source_name", video_path.name)
    payload.setdefault("size_bytes", video_path.stat().st_size)

    probed_metadata = probe_video_metadata(video_path)
    if "embedded_creation_time_utc" in probed_metadata:
        payload["embedded_creation_time_utc"] = probed_metadata["embedded_creation_time_utc"]

    video_metadata = payload.get("video_metadata")
    if not isinstance(video_metadata, dict):
        video_metadata = {}
    video_metadata.update(probed_metadata)

    source_card_path = source_card_dir / video_path.name
    if source_card_path.exists():
        source_stat = source_card_path.stat()
        payload.setdefault("source_card_path", str(source_card_path))
        video_metadata["source_card_modified_unix"] = int(source_stat.st_mtime)
        video_metadata["source_card_modified_time_local"] = local_timestamp(source_stat.st_mtime)

    archive_stat = video_path.stat()
    video_metadata["archive_modified_unix"] = int(archive_stat.st_mtime)
    video_metadata["archive_modified_time_local"] = local_timestamp(archive_stat.st_mtime)

    if video_metadata:
        payload["video_metadata"] = video_metadata

    serialized = json.dumps(payload, indent=2) + "\n"
    if existed and sidecar_path.read_text(encoding="utf-8") == serialized:
        return False

    sidecar_path.write_text(serialized, encoding="utf-8")
    return True


def _parse_dt(value: str | None) -> "datetime | None":
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def match_sessions_by_time(big_videos_dir: Path, sessions_dir: Path) -> int:
    """Fill each sidecar's ``sessions`` by matching embedded creation time to sessions.

    The Canon clock runs a fixed offset ahead of the session local clock. Calibrate
    that offset as the median of (video creation - session start) over the per-session
    camera_recording_reference.json files, then assign a session to a video when the
    session's camera-start (start + offset) lands inside the video's recording window.
    This is robust to a session that was stopped/restarted (whose reference can wrongly
    name the previous file) and to the camera clock being wrong.
    """
    videos: dict[str, tuple[Path, datetime, float]] = {}
    for mov in sorted(big_videos_dir.glob("*.MOV")):
        sidecar = mov.with_suffix(mov.suffix + ".json")
        if not sidecar.exists():
            continue
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        created = _parse_dt(data.get("embedded_creation_time_utc"))
        duration = data.get("video_metadata", {}).get("duration_seconds")
        if created is not None and duration:
            videos[mov.name] = (sidecar, created, float(duration))

    sessions: dict[str, datetime] = {}
    for session_dir in sessions_dir.glob("*"):
        if not session_dir.is_dir():
            continue
        try:
            sessions[session_dir.name] = datetime.strptime(session_dir.name[:19], "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue

    references: dict[str, str] = {}
    for ref in sessions_dir.glob("*/camera_recording_reference.json"):
        try:
            references[ref.parent.name] = json.loads(ref.read_text(encoding="utf-8")).get("source_name")
        except Exception:
            continue

    offsets = [
        (videos[src][1] - sessions[name]).total_seconds()
        for name, src in references.items()
        if name in sessions and src in videos
    ]
    if not offsets:
        return 0
    offset = statistics.median(offsets)

    matched: dict[str, list[str]] = defaultdict(list)
    for name, start in sessions.items():
        cam_start = start + timedelta(seconds=offset)
        for video, (_sidecar, created, duration) in videos.items():
            if created - timedelta(seconds=30) <= cam_start <= created + timedelta(seconds=duration + 5):
                matched[video].append(name)

    changed = 0
    for video, (sidecar, _created, _duration) in videos.items():
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        sess = sorted(matched.get(video, []))
        status = "matched" if sess else "unmatched"
        if (
            data.get("sessions") == sess
            and data.get("match_status") == status
            and data.get("match_method") == "time_window"
        ):
            continue
        data["sessions"] = sess
        data["match_status"] = status
        data["match_method"] = "time_window"
        sidecar.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or refresh .MOV.json sidecars in user-data/big-videos, "
            "including embedded MOV creation time from ffprobe."
        )
    )
    parser.add_argument(
        "--big-videos-dir",
        default=str(DEFAULT_BIG_VIDEOS_DIR),
        help="Directory containing raw camera MOV files.",
    )
    parser.add_argument(
        "--source-card-dir",
        default=str(DEFAULT_SOURCE_CARD_DIR),
        help="Optional source SD-card directory used for source-card mtimes.",
    )
    args = parser.parse_args()

    big_videos_dir = Path(args.big_videos_dir).expanduser()
    if not big_videos_dir.is_absolute():
        big_videos_dir = REPO_ROOT / big_videos_dir
    source_card_dir = Path(args.source_card_dir).expanduser()

    videos = sorted(big_videos_dir.glob("*.MOV"))
    if not videos:
        raise RuntimeError(f"No MOV files found in {big_videos_dir}")

    updated = 0
    for video_path in videos:
        changed = update_metadata(video_path, source_card_dir)
        updated += int(changed)
        status = "updated" if changed else "unchanged"
        print(f"{status}: {video_path.with_suffix(video_path.suffix + '.json')}")

    rematched = match_sessions_by_time(big_videos_dir, REPO_ROOT / "user-data" / "sessions")
    print(f"Processed {len(videos)} MOV files; updated {updated} sidecars; session-matched {rematched} sidecars.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
