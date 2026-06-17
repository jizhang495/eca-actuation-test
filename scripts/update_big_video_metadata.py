#!/usr/bin/env python3
"""Create or refresh sidecar metadata for raw camera videos."""

from __future__ import annotations

import argparse
import json
import time
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

    print(f"Processed {len(videos)} MOV files; updated {updated} sidecars.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
