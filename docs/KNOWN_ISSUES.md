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

6. **Moku:Pro full-run data is not yet reliable enough for primary experiments.**
   The 2026-05-18 Moku full run produced a valid `moku_waveform.csv`, but the data did not match the Tek oscilloscope run well enough to trust yet. Moku logged at 10 kSa/s, which is better time resolution than the Tek full-record capture at about 3.125 kSa/s, but the current-channel amplitude resolution was worse in this run: about 1.62 uA/count for Moku versus about 0.242 uA/count for Tek with the 330 ohm shunt. The zero-current noise was also higher on Moku, roughly a 6.5 uA 5-95% span versus roughly 1.7 uA on Tek.

   The original larger problem was CH1 scaling: the Moku CH1 voltage appeared about 10x low, with the 0.8 V stage reading around 0.08 V, because CH1 used a 10x probe and the app exported the raw Moku input voltage. The Moku path now assumes CH1 is 10x, CH2 is 1x, configures both inputs for `400mVpp`, and writes normalized circuit voltage to `moku_waveform.csv`. This still needs hardware revalidation. The 2026-05-18 export originally covered only about 747.95 s because alignment used the Moku command request time rather than the start acknowledgement time; the code now uses acknowledgement time when available, and that session was rebuilt from the raw Moku CSV to cover about 750.0 s. Keep using the Tek oscilloscope for production actuation runs until Moku CH1 scaling, CH2 noise/resolution, and full-duration coverage are revalidated with a short calibration preset.

## Lower Priority

- `stop.sh`/`stop.ps1` stop broad Python/Node process patterns. That is convenient on a dedicated lab PC but risky on a shared development machine.
- `camera_ready_delay_seconds` is now a general hardware ready delay. The JSON field name is kept for backward compatibility, but a future migration to `ready_delay_seconds` would be clearer.
- The old `StartRecord.cpp` and `StopRecord.cpp` files are legacy examples for labview. The current synchronized camera path uses the long-lived `CameraControl` daemon.
