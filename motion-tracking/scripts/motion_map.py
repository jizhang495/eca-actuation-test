#!/usr/bin/env python3
"""Localize the moving actuator tip via a temporal-variance map.

The free tip is whatever moves, so the per-pixel standard deviation over the
first part of a run lights up the tip's swept path even when the static frame is
a cluttered macro close-up where the thin filament is hard to distinguish from
the body. Use this to read a frame-0 tip seed for a new setup group in
``session_tracking.json`` (convert to the Blender bottom-left origin:
``y_blender = frame_height - y_image``).

    uv run python motion-tracking/scripts/motion_map.py <video.mp4> out.png [dur_s]

The output overlays a JET heatmap (red = most motion) and a 100 px coordinate
grid on the first frame.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def motion_map(video_path: Path, out_png: Path, dur_s: float = 160.0) -> None:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 50.0
    ok, frame0 = cap.read()
    if not ok:
        raise SystemExit(f"could not read {video_path}")
    height, width = frame0.shape[:2]
    n_target = int(dur_s * fps)
    step = max(1, n_target // 80)  # ~80 samples is plenty for a variance map
    frames = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or idx > n_target:
            break
        if idx % step == 0:
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32))
        idx += 1
    cap.release()

    std = np.stack(frames).std(0)
    std_n = cv2.normalize(std, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    heat = cv2.applyColorMap(std_n, cv2.COLORMAP_JET)
    vis = cv2.addWeighted(frame0, 0.55, heat, 0.55, 0)
    for x in range(0, width, 100):
        cv2.line(vis, (x, 0), (x, height), (255, 255, 255), 1)
        cv2.putText(vis, str(x), (x + 2, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    for y in range(0, height, 100):
        cv2.line(vis, (0, y), (width, y), (255, 255, 255), 1)
        cv2.putText(vis, str(y), (2, y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    cv2.imwrite(str(out_png), vis)
    print(f"{video_path.name}: motion heatmap -> {out_png} (samples={len(frames)})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("out_png", type=Path)
    parser.add_argument("dur_s", type=float, nargs="?", default=160.0)
    args = parser.parse_args()
    motion_map(args.video, args.out_png, args.dur_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
