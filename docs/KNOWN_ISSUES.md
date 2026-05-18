# Known Issues And Fix Candidates

This is a live audit list for behavior/documentation mismatches and code risks found while reading the repo.

## Should Fix Soon

1. **Camera sync is logged, not automatically enforced.**
   The current code records camera start/stop offsets relative to measurement `t0`, but it does not fail or flag a run when the offset exceeds the 20 ms acceptance criterion for 50 fps video. Add a runtime warning/error event when the measured offset is greater than 20 ms.

2. **Video download identifies the newest camera movie by timestamp.**
   Auto-download pins the destination session directory, but the source movie is still selected as the newest movie visible on the camera. This is usually correct immediately after a run, but a more robust version should correlate the movie timestamp or file name with the session start/stop time.

3. **Manual `--download-camera` can overlap conceptually with config auto-download.**
   `scripts/run_experiment_config_http.py --download-camera` calls the manual app download endpoint after a run. Full-run presets already enable `auto_download_camera_recording`, so the flag is redundant for those presets. Prefer relying on the preset setting, or change the script to poll the auto-download status when the preset already requests auto-download.

4. **No automated hardware sync test exists.**
   A repeatable LED/electrical marker test should verify camera frame timing against electrical `t0` and stop timing. The acceptance threshold should be less than 20 ms for 50 fps.

5. **No tests cover the FastAPI run lifecycle.**
   Add tests for `start -> status -> auto-stop -> output paths -> auto-download status` using mock instruments. This would catch regressions in agent/browser shared-control behavior.

6. **Moku:Pro acquisition still needs a full-run validation.**
   MokuOS was updated, Moku:Pro bitstreams were downloaded, and a short Moku logger/controller run wrote `moku_waveform.csv`. Before relying on Moku-mode acquisition for experiment data, run a short hardware-connected preset to validate signal polarity/ranges, file download/conversion, and sync.

## Documentation Mismatches Fixed In This Pass

- README and quickstart used the old button labels `Start Measurement` and `Stop Measurement`.
- README described oscilloscope support as not changing scope horizontal settings; the backend now configures full-record scale/record length.
- README and PRD still treated oscilloscope support as future work.
- Camera documentation said video downloads go directly into the newest session folder; the current helper stores raw movies under `user-data/big-videos/` and converts an MP4 into the session folder.
- Quickstart data-output examples omitted oscilloscope waveform files, metadata, and camera MP4 output.
- Mock instrument docs omitted the mock oscilloscope.
- Performance notes said only 500 points are displayed; the UI now keeps about 6000 live points.
- `SETUP.md` suggested moving the backend to port 8001 when port 8000 was busy, which conflicts with the camera service. It now uses port 8002 as the example and calls out the frontend proxy update.
- The Docker section in `SETUP.md` was presented as ready-to-run even though there are no Dockerfiles. It is now marked as an example only.

## Lower Priority

- `stop.sh`/`stop.ps1` stop broad Python/Node process patterns. That is convenient on a dedicated lab PC but risky on a shared development machine.
- `camera_ready_delay_seconds` is now a general hardware ready delay. The JSON field name is kept for backward compatibility, but a future migration to `ready_delay_seconds` would be clearer.
- The old `StartRecord.cpp` and `StopRecord.cpp` files are legacy examples. The current synchronized camera path uses the long-lived `CameraControl` daemon.
