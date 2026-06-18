# Motion Tracking

This folder automates the Blender marker workflow with OpenCV and keeps the
manual Blender exports as validation data.

The current tracker uses the first manual marker coordinate as the actuator tip
seed and the first reference marker coordinate as a fixed clamp/reference point.
It does not track the clamp over time because the clamp is assumed fixed. Video
resolution, FPS, and frame count are read from OpenCV video metadata at runtime,
so the same script works for the current 720p/50 fps and 1080p/25 fps videos and
future 720p/50 fps videos.

The output coordinates use the same pixel convention as the Blender exports.
Automated displacement is always `tip - fixed_reference`.

## Current Status

The reusable script is `motion-tracking/scripts/track_actuator_opencv.py`.

Current capabilities:

- Batch-tracks the seven existing videos configured in
  `motion-tracking/config/opencv_tracking_videos.json`.
- Exports one automated CSV per video under `motion-tracking/user-data/opencv/`.
- Compares OpenCV tracking against manual Blender CSV exports.
- Plots full-duration comparison SVGs and relay-window comparison SVGs.
- Writes annotated preview MP4s so the tracker position can be inspected on top
  of the video.
- Uses the fixed clamp coordinate from the first manual reference marker.
- Preserves raw OpenCV coordinates when filtering is enabled.

The old 720p/50 fps videos (`6998`, `6999`, `7000`) use template matching and
currently agree with manual tracking to about 1.7-1.9 px radial RMSE. The later
1080p/25 fps videos (`7003`-`7006`) use the dark-endpoint tracker and are more
mixed: `7005` is good, `7004` and `7006` are usable but need inspection, and
`7003` is still noticeably worse.

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

For videos that use the dark-endpoint tracker, the CSV keeps both raw and filtered coordinates. `raw_tip_x_px`,
`raw_tip_y_px`, `raw_displacement_x_px`, and `raw_displacement_y_px` preserve the detector output. The normal
`tip_*` and `displacement_*` columns use the configured jump rejection and short median filter for comparison and
plotting.

To process one video:

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py --video-id 7006
```

To inspect where the tracker lands on the source video, write an annotated preview:

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py --no-track --no-compare --video-id 7006 --write-preview --preview-start-s 45 --preview-end-s 60 --preview-stride 1
```

To write previews around all configured relay events for one video:

```bash
uv run python motion-tracking/scripts/track_actuator_opencv.py --no-track --no-compare --video-id 7006 --write-relay-previews --preview-stride 1
```

Preview videos are written under `motion-tracking/user-data/opencv/previews/`. The overlay uses a cyan circle for the
filtered OpenCV tip, a magenta cross for the raw detector tip when filtering moved it, a green cross for the fixed
clamp/reference point, an orange cross for the manual tip when a manual CSV is available, and a short cyan trail for
recent filtered OpenCV tip positions.

## Seed Config

Video paths and initial pixel coordinates are stored in `motion-tracking/config/opencv_tracking_videos.json`.

The 1280x720 manual CSVs for `6998`, `6999`, and `7000` were exported with the opposite displacement sign from the later videos: their first CSV row matches `clamp - tip`. The config still tracks the visible free tip and fixed clamp/reference point, then uses `manual_displacement_scale: -1.0` only when comparing against those old manual CSVs. Automated CSV output is always `tip - fixed_reference`.

The relay-event comparison window is configured in the same JSON with `relay_event_first_s`, `relay_event_period_s`, and `relay_event_window_s`.

Tracker filtering is configured with `smoothing_window_s` and `jump_reject_max_px`. Set `smoothing_window_s` to `0`
and `jump_reject_max_px` to `0` to export unfiltered coordinates only.

## Batch Tracking Session Videos (no manual ground truth)

`track_actuator_opencv.py` is the validation workflow: a handful of videos, each
hand-seeded from its Blender markers, compared against manual CSVs. To get fast
preliminary deflection traces for *all* session videos that have never been
manually tracked, use the thin wrapper:

```bash
uv run python motion-tracking/scripts/track_session_videos.py            # all configured groups
uv run python motion-tracking/scripts/track_session_videos.py --dry-run  # list jobs + seeds
uv run python motion-tracking/scripts/track_session_videos.py --group 2026-06-09_sweeps
uv run python motion-tracking/scripts/track_session_videos.py --session 2026-06-12_17-14-27 --write-preview
```

It reuses `track_video` and `write_preview_video` unchanged, but is built for the
production workflow instead of validation:

- **Seeds are shared per camera setup group** (a day/rig where the camera did not
  move), not one hand-tuned entry per video, in
  `motion-tracking/config/session_tracking.json`. A session may override the group
  seed if its framing drifted. Seeds use the same Blender bottom-left pixel origin
  as the validation config (`y_blender = frame_height - y_image`).
- **The CSV is written into the session folder** as `<video_id>_opencv.csv`, next
  to `moku_waveform.csv`, so motion and electrical data sit together. Columns are
  identical to the validation CSVs.
- **No Blender comparison** (there is no ground truth). Annotated preview MP4s and
  displacement-vs-time plots are the QA mechanism.

These are all 1280x720 / 50 fps videos, the format where template matching agreed
with manual tracking to ~1.7-1.9 px. This is for preliminary analysis; track
manually in Blender later for publication-quality numbers.

### Tracker choice for sweeps

The session default sets `use_lk_prediction: true` with
`max_template_correction_px: 6.0`. A fixed frame-0 template alone (the validation
default) drifts during the large, slow excursions of the low-frequency sweeps:
when the tip swings near the dark actuator body, the light-background template
stops matching the endpoint and the marker sticks in the background, clipping the
low-frequency peaks and flattening the Bode rolloff. LK optical-flow prediction
follows the endpoint frame-to-frame and the fixed template corrects ≤6 px/frame.
On the step videos (smaller, slower motion) LK+template is identical to plain
template, so one method covers both. `dark_endpoint` was tried and fails here: it
cannot isolate the thin filament tip from the body and falls back on most frames.

### Seeding a new setup group

The free tip is whatever *moves*, so the per-pixel temporal variance over the
first ~160 s localizes it even when the static frame is a cluttered macro
close-up (hand-reading the tip pixel off such a frame is unreliable — the
filament is easy to confuse with the body and the tip reaches farther into the
background than it looks). `autoseed_tip.py` uses that to print paste-ready
seeds:

```bash
uv run python motion-tracking/scripts/autoseed_tip.py <video.mp4> /tmp/seed.png
# -> tip_seed_px=[768,140] reference_seed_px=[212,387]   (already Blender origin)
```

Paste those into a group in `session_tracking.json`. Sessions from one camera-day
usually share a seed; a session whose framing drifted gets a per-session override
(`{"path": ..., "tip_seed_px": ..., "reference_seed_px": ...}`). `motion_map.py`
writes the raw heatmap overlay if you want to eyeball the seed instead.

Then `--dry-run` to confirm, track, and QA the displacement plot: a **flat** trace
with a perfect match score means the seed missed the moving tip (it locked a
static feature), so re-seed that session.

## Validation Snapshot

The latest generated summaries are:

- `motion-tracking/user-data/opencv/comparisons/summary.csv`
- `motion-tracking/user-data/opencv/comparisons/event_window_summary.csv`

Current full-video radial error against manual Blender tracking:

| Video | Method | Full RMSE (px) | Full p95 (px) | Relay-window RMSE (px) | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| 6998 | Template | 1.908 | 4.162 | 2.017 | Good baseline. |
| 6999 | Template | 1.665 | 3.426 | 1.685 | Good baseline. |
| 7000 | Template | 1.835 | 4.384 | 1.840 | Good baseline. |
| 7003 | Dark endpoint + filter | 6.465 | 10.193 | 6.811 | Worst current match; likely endpoint drift or segmentation error. |
| 7004 | Dark endpoint + filter | 4.118 | 6.595 | 4.162 | Usable but should be inspected around relay windows. |
| 7005 | Dark endpoint + filter | 1.407 | 2.680 | 1.680 | Best later-video result. |
| 7006 | Dark endpoint + filter | 2.593 | 4.990 | 3.553 | Normal error is moderate, but one large outlier remains. |

Important findings:

- Mild median filtering reduces frame-to-frame jitter but does not fix detector
  failures. The largest remaining errors in `7003` and `7006` are probably not
  simple one-frame noise.
- The dark-endpoint approach is sensitive to contrast, lighting, and whether
  the dark actuator edge is cleanly segmented from the background.
- The manual Blender result is still the ground-truth comparison for now, but it
  is also a point-tracking workflow rather than a full cantilever-shape
  measurement.
- For future experiments, 720p/50 fps should help because it gives twice the
  temporal resolution of the 1080p/25 fps videos while still being easy to
  process.

## Preview Review

Preview videos are the main way to diagnose whether the tracker is following the
right physical point. For example, relay-window previews for `7006` were
generated under:

```text
motion-tracking/user-data/opencv/previews/
```

Example output:

```text
7006_tracking_preview_45s-55s.mp4
7006_tracking_preview_95s-105s.mp4
...
7006_tracking_preview_695s-705s.mp4
```

Use these previews to classify errors before changing the algorithm:

- If the cyan marker follows the visible tip but disagrees with Blender, check
  the manual marker definition or coordinate convention.
- If the cyan marker occasionally jumps to another dark edge and returns, tune
  jump rejection, ROI size, and segmentation thresholds.
- If the cyan marker slowly rides along the wrong part of the actuator edge, the
  endpoint detector needs a better geometric model.

## Next Work

### Easy Improvements

These are low-risk changes that keep the current script structure.

- Generate relay-window previews for all videos, not just `7006`, then inspect
  the largest-error windows listed in `event_window_summary.csv`.
- Tune `smoothing_window_s`, `jump_reject_max_px`, and dark-endpoint ROI
  parameters per video after preview inspection.
- Add a small QA report that links each comparison SVG, relay-window SVG, and
  preview MP4 from one HTML or Markdown index.
- Add automatic warnings when the filtered tip differs from the raw tip by more
  than a threshold for many consecutive frames.
- Add config entries for future 720p/50 fps videos as soon as the first manual
  seed point and clamp coordinate are known.

Expected impact: less visible jitter and faster manual review. These changes
will not solve the fundamental problem when the endpoint detector locks onto the
wrong contour.

### Better Improvements

These change the tracker but still stay within conventional OpenCV methods.

- Replace single-point dark endpoint detection with contour-based cantilever
  detection inside a clamp-to-tip ROI.
- Fit the visible actuator edge or centerline using thresholding, morphology,
  connected components, and robust line/curve fitting.
- Use the previous frame only as a prediction, then choose the contour that is
  physically consistent with the clamp, expected actuator length, and expected
  bending direction.
- Add a temporal model, such as a Kalman filter or robust spline smoothing, that
  rejects physically impossible jumps without flattening real relay-driven
  motion.
- Export confidence metrics such as contour area, distance from prediction,
  fitted curve residual, and endpoint confidence.

Expected impact: better robustness for `7003`, `7004`, and `7006`, and fewer
large outliers than a pure dark-pixel endpoint tracker.

### Bigger Step

The larger and more useful upgrade is to stop treating the video as a two-point
tracking problem and instead track the actuator shape.

Proposed output per frame:

- Clamp point.
- Free tip point.
- Centerline or edge polyline along the cantilever.
- Tip displacement.
- Bending angle.
- Curvature or fitted polynomial/spline parameters.
- Confidence/quality flags.

Possible approaches:

- Classical segmentation: crop a fixed ROI around the actuator, segment the dark
  cantilever, skeletonize or fit its boundary, then extract the furthest point
  and centerline.
- Semi-automatic segmentation: use a small number of manually labeled frames to
  tune a color/brightness model, then track the whole cantilever through the
  run.
- AI-assisted segmentation: use SAM-style segmentation or a small trained model
  to identify the actuator mask, then use OpenCV geometry to extract tip and
  bending shape.
- Specialized pose/keypoint tracking: use tools such as DeepLabCut only if the
  target point remains ambiguous after OpenCV improvements.

Expected impact: this would support more than tip displacement. It would allow
charge-strain and waveform-response analysis using shape, bending angle, and
curvature over time, which is closer to the actual actuator mechanics.
