#!/usr/bin/env python3
"""Transcode archived big-video MOVs into their matched session folders as MP4.

Each ``user-data/big-videos/MVI_*.MOV`` has a sidecar whose ``sessions`` field
(filled by ``update_big_video_metadata.py``) names the session(s) it belongs to.
This writes ``<session>/<video_id>.mp4`` for every matched video that does not yet
have one, so the session has a small, trackable/viewable copy beside its data.

Hardware VAAPI H.264 is the default (fast for bulk 4 GB clips); pass ``--libx264``
for the portable CRF encoder. Unmatched videos are skipped.

    uv run python scripts/convert_big_videos_to_sessions.py
    uv run python scripts/convert_big_videos_to_sessions.py --min 7043 --max 7072
    uv run python scripts/convert_big_videos_to_sessions.py --libx264 --crf 22 --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIG = REPO_ROOT / "user-data" / "big-videos"
SESSIONS = REPO_ROOT / "user-data" / "sessions"


def duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        ).stdout
        return float(out or 0)
    except Exception:
        return 0.0


def ffmpeg_cmd(src: Path, dst: Path, args: argparse.Namespace) -> list[str]:
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-y" if args.force else "-n"]
    if args.libx264:
        return base + ["-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                       "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(dst)]
    return base + ["-hwaccel", "vaapi", "-vaapi_device", args.vaapi_device,
                   "-i", str(src), "-vf", "format=nv12,hwupload",
                   "-c:v", "h264_vaapi", "-qp", str(args.qp), "-an",
                   "-movflags", "+faststart", str(dst)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min", type=int, default=0, help="Only videos with number >= this.")
    parser.add_argument("--max", type=int, default=10**9, help="Only videos with number <= this.")
    parser.add_argument("--libx264", action="store_true", help="Use libx264 CRF instead of VAAPI.")
    parser.add_argument("--crf", type=int, default=22)
    parser.add_argument("--qp", type=int, default=23, help="VAAPI quantizer.")
    parser.add_argument("--vaapi-device", default="/dev/dri/renderD128")
    parser.add_argument("--force", action="store_true", help="Overwrite existing session MP4s.")
    args = parser.parse_args()

    ok = skip = fail = 0
    for sidecar in sorted(BIG.glob("MVI_*.MOV.json")):
        try:
            num = int(sidecar.name.split("_")[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        if not (args.min <= num <= args.max):
            continue
        data = json.loads(sidecar.read_text())
        sessions = data.get("sessions") or []
        src = BIG / sidecar.stem  # MVI_xxxx.MOV
        if not sessions or not src.exists():
            continue
        dst = SESSIONS / sessions[0] / (src.stem + ".mp4")
        if dst.exists() and not args.force and abs(duration(dst) - duration(src)) < 2:
            print(f"skip {dst.parent.name}/{dst.name}", flush=True)
            skip += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(ffmpeg_cmd(src, dst, args), capture_output=True, text=True)
        if result.returncode == 0 and abs(duration(dst) - duration(src)) < 2:
            print(f"OK   {dst.parent.name}/{dst.name}  {dst.stat().st_size/1e6:.0f}MB", flush=True)
            ok += 1
        else:
            print(f"FAIL {dst.name}: {result.stderr[-200:]}", flush=True)
            fail += 1
    print(f"=== converted {ok}, skipped {skip}, failed {fail} ===")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
