#!/usr/bin/env python3
"""Autoseed and motion-track session MP4s that are not tracked yet.

For a batch of new sessions with no manual Blender seeds and a camera framing that
drifts between runs, this autoseeds each session's ``MVI_*.mp4`` with
``autoseed_tip`` (motion-peak tip detection) and tracks it with the same
LK+template tracker as ``track_session_videos.py``, writing
``<session>/<video_id>_opencv.csv``.

Use the group config (``session_tracking.json`` + ``track_session_videos.py``)
when a stable per-day seed is known; use this when the framing drifts per run and
you want a zero-config pass. Verify with the displacement plot / preview as usual;
a flat trace means the autoseed missed the moving tip.

    uv run python motion-tracking/scripts/autotrack_sessions.py user-data/sessions/2026-06-18_*
    uv run python motion-tracking/scripts/autotrack_sessions.py --force <session-dir> ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[2]
sys.path.insert(0, str(SCRIPT.parent))
import track_actuator_opencv as TRK  # noqa: E402
import autoseed_tip  # noqa: E402


def default_params() -> dict:
    cfg = json.loads((REPO_ROOT / "motion-tracking" / "config" / "session_tracking.json").read_text())
    return {**cfg["default_params"], "method": "template"}  # use_lk_prediction stays true


def track_session(session_dir: Path, params: dict, force: bool) -> str:
    mp4s = sorted(session_dir.glob("MVI_*.mp4"))
    if not mp4s:
        return "no-mp4"
    mp4 = mp4s[0]
    out = session_dir / (mp4.stem + "_opencv.csv")
    if out.exists() and not force:
        return "skip"
    tip_img, ref_img = autoseed_tip.autoseed_tip(mp4)
    cap = cv2.VideoCapture(str(mp4))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    vc = TRK.VideoConfig(
        video_id=mp4.stem,
        video_path=mp4,
        manual_csv=None,
        manual_displacement_scale=1.0,
        tip_seed_px=(float(tip_img[0]), float(height - tip_img[1])),
        reference_seed_px=(float(ref_img[0]), float(height - ref_img[1])),
        params=params,
    )
    TRK.track_video(vc, session_dir)
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="+", type=Path, help="Session directories (shell globs expand to these).")
    parser.add_argument("--force", action="store_true", help="Re-track even if an _opencv.csv already exists.")
    args = parser.parse_args()

    counts: dict[str, int] = {}
    for session_dir in args.sessions:
        if not session_dir.is_dir():
            continue
        try:
            status = track_session(session_dir, default_params(), args.force)
        except Exception as exc:  # keep going through the batch
            status = "fail"
            print(f"FAIL {session_dir.name}: {exc}", flush=True)
        else:
            print(f"{status:5s} {session_dir.name}", flush=True)
        counts[status] = counts.get(status, 0) + 1
    print(f"=== {counts} ===", flush=True)
    return 1 if counts.get("fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
