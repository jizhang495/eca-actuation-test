# Video → Analysis Pipeline

End-to-end steps to take a batch of camera recordings off the SD card and turn
them into the per-session deflection trace and the 4-panel
`moku_waveform_analysis.svg` (voltage / current / charge / deflection).

Each measurement run already leaves `user-data/sessions/<session>/` with the
electrical data (`moku_waveform.csv`, `config.json`, …) and a
`camera_recording_reference.json` naming the camera movie. The camera movies
themselves stay on the SD card until offloaded. This doc covers everything after
the run. For the live per-run download see [CAMERA.md](CAMERA.md); for tracker
internals see [../motion-tracking/README.md](../motion-tracking/README.md); for
the charge model see [CHARGE_TRANSFER_ANALYSIS.md](CHARGE_TRANSFER_ANALYSIS.md).

## 1. Offload the SD card

Copy the raw MOVs to the archive (they are ~4 GB each). Plain `cp`; verify sizes
before deleting anything from the card.

```bash
cp /media/jz/EOS_DIGITAL/DCIM/100CANON/*.MOV user-data/big-videos/
```

## 2. Sidecar metadata + session matching

```bash
uv run python3 scripts/update_big_video_metadata.py
```

Writes/refreshes `user-data/big-videos/MVI_*.MOV.json`: ffprobe metadata
(embedded creation time, duration, codec, dimensions) and the `sessions` list.

Matching is **by time, not by the camera reference**. The Canon clock runs a
fixed offset ahead of the session clock (calibrated as the median of
`creation_time - session_start` over all `camera_recording_reference.json`
files); a session is assigned to a video when its camera-start
(`start + offset`) falls inside the video's recording window. This is robust to a
session that was stopped/restarted, whose reference can wrongly name the
*previous* movie (`match_status: matched`, `match_method: time_window`; videos
with no session — test clips — stay `unmatched`).

## 3. Transcode into the session folders

```bash
uv run python3 scripts/convert_big_videos_to_sessions.py            # VAAPI H.264, all matched
uv run python3 scripts/convert_big_videos_to_sessions.py --min 7043 --max 7072
```

Writes `<session>/<video_id>.mp4` (small, trackable) for every matched MOV.
Defaults to hardware VAAPI (`/dev/dri/renderD128`, qp 23); `--libx264` for the
portable CRF encoder. For a single ad-hoc file, `scripts/convert_mov_to_mp4.py`.

## 4. Motion-track the session videos

Two paths, both writing `<session>/<video_id>_opencv.csv` (LK + fixed-template
tracker):

- **Stable per-day framing** — add a setup-group seed to
  `motion-tracking/config/session_tracking.json` and run
  `motion-tracking/scripts/track_session_videos.py`.
- **Framing drifts per run (zero-config)** — autoseed each video's tip from where
  it moves:

  ```bash
  uv run python motion-tracking/scripts/autotrack_sessions.py user-data/sessions/2026-06-18_*
  ```

QA: a flat displacement trace with a perfect match score means the seed missed
the moving tip — re-seed that one (see the tracking README).

## 5. Charge-transfer model (relay/step runs)

```bash
uv run python3 scripts/analyze_charge_transfer.py user-data/sessions/<session>
```

Writes `<session>/moku_waveform_charge_transfer_timeseries.csv` (per-edge local
baselines + modeled current → signed cumulative charge). The analysis plot reads
this instead of integrating raw current (which would just accumulate the ~uA
offset). Sweep runs have no relay edges and get no charge panel.

## 6. Plot

```bash
./plot-moku.sh
```

Writes `<session>/moku_waveform_analysis.svg`: median-binned voltage, current,
charge (from step 5, when present), and deflection `atan(dy/dx)` (from step 4,
preferring manual Blender tracking, else the OpenCV CSV), sharing the time axis.
`plot-moku.sh` stops at the first already-plotted session, so delete the existing
`moku_waveform_analysis.svg` (or call `scripts/plot_oscilloscope_waveform.py`
directly) to regenerate.

## One-shot for a fresh batch

```bash
cp /media/jz/EOS_DIGITAL/DCIM/100CANON/*.MOV user-data/big-videos/
uv run python3 scripts/update_big_video_metadata.py
uv run python3 scripts/convert_big_videos_to_sessions.py
uv run python  motion-tracking/scripts/autotrack_sessions.py user-data/sessions/<date>_*
for d in user-data/sessions/<date>_*; do uv run python3 scripts/analyze_charge_transfer.py "$d"; done
# delete stale analysis SVGs, then:
./plot-moku.sh
```
