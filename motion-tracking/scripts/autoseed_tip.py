#!/usr/bin/env python3
"""Auto-pick a frame-0 tip seed for a session video from where the tip moves.

Hand-reading the free-tip pixel off a static macro close-up is error-prone: the
thin filament is easy to confuse with the body, and the tip often extends much
farther into the light background than expected. This finds it automatically:

1. Per-pixel temporal standard deviation over the first ~160 s -> the moving
   tip's path. Its blurred peak is on the tip's swept arc.
2. The frame-0 tip is the dark filament pixel nearest that motion peak (so the
   seed lands on the filament *at frame 0*, where the template is cropped).
3. The reference/clamp is the centroid of dark, low-variance, non-border pixels
   (a static body point; it only sets the displacement offset, not amplitude).

Prints image coords and the Blender bottom-left coords used in
``session_tracking.json`` (``y_blender = frame_height - y_image``). Pass an output
PNG to also write a debug overlay (cyan = motion peak, red = tip, green = ref).

    uv run python motion-tracking/scripts/autoseed_tip.py <video.mp4> [debug.png]

Works well for the 1280x720 filament-on-light-background rigs. Verify the result
by tracking and checking the displacement plot is not flat; a cluttered or very
low-contrast frame may still need a hand seed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def autoseed_tip(video_path: Path, dur_s: float = 160.0, out_png: Path | None = None):
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 50.0
    ok, frame0 = cap.read()
    if not ok:
        raise SystemExit(f"could not read {video_path}")
    height, width = frame0.shape[:2]
    n_target = int(dur_s * fps)
    stride = max(1, n_target // 80)
    frames, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok or idx > n_target:
            break
        if idx % stride == 0:
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32))
        idx += 1
    cap.release()

    std = np.stack(frames).std(0)
    py, px = np.unravel_index(int(np.argmax(cv2.GaussianBlur(std, (9, 9), 0))), std.shape)

    gray0 = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray0 < 130)
    tip_i = int(np.argmin((xs - px) ** 2 + (ys - py) ** 2))
    tip = (int(xs[tip_i]), int(ys[tip_i]))

    margin = 40
    var_at_dark = std[ys, xs]
    inb = (xs > margin) & (xs < width - margin) & (ys > margin) & (ys < height - margin)
    static = inb & (var_at_dark < np.percentile(var_at_dark[inb], 25))
    ref = (int(xs[static].mean()), int(ys[static].mean()))

    if out_png is not None:
        vis = frame0.copy()
        cv2.drawMarker(vis, (int(px), int(py)), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 24, 2)
        cv2.circle(vis, tip, 12, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.drawMarker(vis, ref, (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
        cv2.imwrite(str(out_png), vis)
    print(f"{video_path.name}: tip_img={tip} ref_img={ref}  "
          f"tip_seed_px=[{tip[0]},{height - tip[1]}] reference_seed_px=[{ref[0]},{height - ref[1]}]")
    return tip, ref


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("debug_png", type=Path, nargs="?")
    parser.add_argument("--dur-s", type=float, default=160.0)
    args = parser.parse_args()
    autoseed_tip(args.video, args.dur_s, args.debug_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
