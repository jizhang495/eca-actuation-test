#!/usr/bin/env python3
"""Batch-track session videos that have no manual Blender ground truth.

This is a thin wrapper around ``track_actuator_opencv.py``. It reuses the same
tracking and preview code, but is built for the production workflow rather than
the validation workflow:

- Seeds are shared per camera *setup group* (a day/rig where the camera did not
  move) instead of one hand-tuned entry per video. A session may override the
  group seed if its framing drifted.
- The automated tip-displacement CSV is written **into the session folder**
  (next to ``moku_waveform.csv``) so motion and electrical data sit together for
  the coupled theta-vs-voltage/charge analysis.
- There is no Blender comparison step (no ground truth for these videos);
  annotated preview MP4s are the QA mechanism instead.

These are 1280x720 / 50 fps videos, the format where template matching agreed
with manual tracking to ~1.7-1.9 px. This is meant for fast preliminary
results; track manually in Blender later for publication-quality numbers.

Config: ``motion-tracking/config/session_tracking.json``

    {
      "default_params": { ... template tracker params ... },
      "groups": [
        {
          "name": "2026-06-09_sweeps",
          "tip_seed_px": [x, y],          # Blender bottom-left origin (y up)
          "reference_seed_px": [x, y],    # fixed point; only sets the offset
          "params": { ... optional overrides ... },
          "sessions": [
            "user-data/sessions/<dir>",                 # uses group seed
            {"path": "user-data/sessions/<dir>",        # per-session override
             "tip_seed_px": [x, y], "reference_seed_px": [x, y]}
          ]
        }
      ]
    }

Run all configured sessions:

    uv run python motion-tracking/scripts/track_session_videos.py

Pilot one or two sessions and write a short preview:

    uv run python motion-tracking/scripts/track_session_videos.py \
        --session 2026-06-09_16-16-55 --session 2026-06-12_17-14-27 \
        --write-preview --preview-start-s 0 --preview-end-s 30 --preview-stride 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
DEFAULT_CONFIG = REPO_ROOT / "motion-tracking" / "config" / "session_tracking.json"

# Reuse the tracking + preview code (functions only; its main() is __main__-guarded).
sys.path.insert(0, str(SCRIPT_PATH.parent))
import track_actuator_opencv as TRK  # noqa: E402


def _seed(value: Any) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def _find_session_video(session_dir: Path) -> Path | None:
    """Return the single MVI_*.mp4 (or any *.mp4) in a session folder."""
    candidates = sorted(session_dir.glob("MVI_*.mp4")) or sorted(session_dir.glob("*.mp4"))
    if not candidates:
        return None
    if len(candidates) > 1:
        raise RuntimeError(
            f"{session_dir}: expected one mp4, found {[p.name for p in candidates]}"
        )
    return candidates[0]


def build_jobs(config: dict, repo_root: Path) -> list[Any]:
    """Flatten groups -> per-session VideoConfig jobs."""
    default_params = config.get("default_params", {})
    jobs: list[Any] = []
    for group in config.get("groups", []):
        group_params = {**default_params, **group.get("params", {})}
        group_tip = _seed(group["tip_seed_px"])
        group_ref = _seed(group["reference_seed_px"])
        for entry in group.get("sessions", []):
            if isinstance(entry, str):
                rel_path, tip, ref, params = entry, group_tip, group_ref, group_params
            else:
                rel_path = entry["path"]
                tip = _seed(entry["tip_seed_px"]) if "tip_seed_px" in entry else group_tip
                ref = _seed(entry["reference_seed_px"]) if "reference_seed_px" in entry else group_ref
                params = {**group_params, **entry.get("params", {})}

            session_dir = (repo_root / rel_path).resolve()
            if not session_dir.is_dir():
                print(f"SKIP  missing session dir: {rel_path}")
                continue
            video_path = _find_session_video(session_dir)
            if video_path is None:
                print(f"SKIP  no mp4 in: {rel_path}")
                continue

            jobs.append(
                (
                    group.get("name", "?"),
                    session_dir,
                    TRK.VideoConfig(
                        video_id=video_path.stem,
                        video_path=video_path,
                        manual_csv=None,
                        manual_displacement_scale=1.0,
                        tip_seed_px=tip,
                        reference_seed_px=ref,
                        params=params,
                    ),
                )
            )
    return jobs


def matches_filter(session_dir: Path, group_name: str, args: argparse.Namespace) -> bool:
    if args.group and group_name not in args.group:
        return False
    if args.session and not any(s in session_dir.name for s in args.session):
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--group", action="append", help="Only this group name. Repeatable.")
    parser.add_argument("--session", action="append", help="Substring of session dir name. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="List jobs without tracking.")
    parser.add_argument("--write-preview", action="store_true", help="Write an annotated preview MP4 into the session.")
    parser.add_argument("--preview-start-s", type=float)
    parser.add_argument("--preview-end-s", type=float)
    parser.add_argument("--preview-stride", type=int, default=2)
    parser.add_argument("--preview-max-width", type=int, default=1280)
    parser.add_argument("--preview-trail-s", type=float, default=2.0)
    parser.add_argument("--limit-frames", type=int, help="Debug: stop after this many frames.")
    parser.add_argument("--progress-every", type=int, default=5000)
    return parser.parse_args()


def main() -> int:
    import json

    args = parse_args()
    repo_root = args.repo_root.resolve()
    with args.config.resolve().open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    jobs = [job for job in build_jobs(config, repo_root) if matches_filter(job[1], job[0], args)]
    if not jobs:
        print("No matching sessions.")
        return 1

    print(f"Matched {len(jobs)} session(s):")
    for group_name, session_dir, cfg in jobs:
        print(f"  [{group_name}] {session_dir.name}/{cfg.video_path.name}  "
              f"tip={cfg.tip_seed_px} ref={cfg.reference_seed_px} method={cfg.params.get('method', 'template')}")
    if args.dry_run:
        return 0

    failures = 0
    for group_name, session_dir, cfg in jobs:
        print(f"\n=== {session_dir.name}/{cfg.video_path.name} ===")
        try:
            csv_path = TRK.track_video(
                cfg,
                session_dir,
                limit_frames=args.limit_frames,
                progress_every=args.progress_every,
            )
            print(f"wrote {csv_path}")
            if args.write_preview:
                preview = TRK.write_preview_video(
                    cfg,
                    csv_path,
                    session_dir,
                    start_s=args.preview_start_s,
                    end_s=args.preview_end_s,
                    frame_stride=args.preview_stride,
                    max_width=args.preview_max_width,
                    trail_s=args.preview_trail_s,
                )
                print(f"wrote {preview}")
        except Exception as exc:  # keep going through the batch
            failures += 1
            print(f"FAILED {session_dir.name}: {exc}")

    print(f"\nDone: {len(jobs) - failures} ok, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
