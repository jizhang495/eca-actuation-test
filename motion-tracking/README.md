# Motion Tracking

This folder automates the Blender marker workflow with OpenCV.

The tracker uses the first manual marker coordinate as the seed for the actuator tip and the first reference marker coordinate as a fixed clamp/reference point. It does not track the clamp over time. Video resolution, FPS, and frame count are read from OpenCV video metadata at runtime, so the same script works for the current 720p/50 fps and 1080p/25 fps videos and future 720p/50 fps videos.

The output coordinates use the same pixel convention as the Blender exports. Automated displacement is always `tip - fixed_reference`.

## Run

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py
```

Outputs are written under `motion-tracking/user-data/opencv/`:

- `<video_id>_opencv.csv`: automated per-frame tip/reference displacement.
- `comparisons/<video_id>_comparison.csv`: joined automated/manual rows.
- `comparisons/<video_id>_comparison.svg`: full-duration displacement and error plot with relay-edge markers.
- `comparisons/<video_id>_relay_windows.svg`: overlaid windows around the relay edges, currently +/-5 s around 50 s, 100 s, 150 s, etc.
- `comparisons/summary.csv`: aggregate validation metrics over the full video.
- `comparisons/event_window_summary.csv`: validation metrics only inside the relay-edge windows.

To process one video:

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py --video-id 7006
```

To inspect where the tracker lands on the source video, write an annotated preview:

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py --no-track --no-compare --video-id 7006 --write-preview --preview-start-s 45 --preview-end-s 60 --preview-stride 1
```

Preview videos are written under `motion-tracking/user-data/opencv/previews/`. The overlay uses a cyan circle for the OpenCV tip, a green cross for the fixed clamp/reference point, an orange cross for the manual tip when a manual CSV is available, and a short cyan trail for recent OpenCV tip positions.

## Seed Config

Video paths and initial pixel coordinates are stored in `motion-tracking/config/opencv_tracking_videos.json`.

The 1280x720 manual CSVs for `6998`, `6999`, and `7000` were exported with the opposite displacement sign from the later videos: their first CSV row matches `clamp - tip`. The config still tracks the visible free tip and fixed clamp/reference point, then uses `manual_displacement_scale: -1.0` only when comparing against those old manual CSVs. Automated CSV output is always `tip - fixed_reference`.

The relay-event comparison window is configured in the same JSON with `relay_event_first_s`, `relay_event_period_s`, and `relay_event_window_s`.
