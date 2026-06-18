# Charge Transfer Analysis

This document describes how relay-edge current peaks and whole-run charge transfer are modeled from high-rate waveform data.

The implementation is:

```text
scripts/analyze_charge_transfer.py
```

It accepts either a session directory or a waveform CSV:

```bash
uv run python3 scripts/analyze_charge_transfer.py \
  user-data/sessions/<session>
```

For the current Moku 750 s run:

```bash
uv run python3 scripts/analyze_charge_transfer.py \
  user-data/sessions/2026-05-22_12-45-22_step_voltage_relay2_750s_moku
```

For an oscilloscope run:

```bash
uv run python3 scripts/analyze_charge_transfer.py \
  user-data/sessions/2026-05-18_17-58-23_step_voltage_relay2_750s_oscilloscope
```

## Inputs

The script expects these files in the session directory:

- `moku_waveform.csv` or `oscilloscope_waveform.csv`
- `config.json`

The waveform CSV must contain:

- `time`
- `ch1_voltage`
- `ch2_voltage`

The config file is used to recover relay edge times from `relay_ch*_stages`. Closed relay stage starts are treated as `close` edges; closed relay stage ends are treated as `open` edges.

Current is calculated from the CH2 shunt voltage:

```text
current_mA = ch2_voltage / (shunt_ohms * amplifier_gain) * 1000
```

The default shunt is `330 ohm`, and the default amplifier gain is `1`.

## Baseline Handling

The script applies two baseline corrections.

First, a whole-run zero-current offset is subtracted from all current samples. By default this is the median current from `0-20 s`, which lies inside the initial 0 V stage of the 750 s preset.

This protects the full-run charge plot from accumulating instrument offset over hundreds of seconds.

Relevant options:

```bash
--zero-baseline-start 0
--zero-baseline-end 20
--disable-zero-baseline
```

Second, each relay edge has a local pre-edge baseline. By default this is the median current from `-0.2 s` to `-0.02 s` relative to the scheduled relay edge. This local baseline is used for fitting and edge-window charge reporting.

Relevant options:

```bash
--baseline-start -0.2
--baseline-end -0.02
```

## Flat Regions

Current outside relay-edge windows is modeled using median bins.

By default, the script excludes `0.02 s` before each relay edge through `1.0 s` after each relay edge, then takes `200 ms` median bins of the remaining current. The binned values are interpolated onto the output time grid.

This removes most high-rate noise while preserving slow current drift or offset trends.

Relevant options:

```bash
--flat-bin-ms 200
--edge-exclude-before 0.02
--fit-end 1.0
--output-dt-ms 1.0
```

## Relay-Edge Fitting

The relay switching peaks are modeled separately so that the charge associated with fast charging and discharging is not lost to median smoothing.

For each relay edge:

1. Extract a local window around the scheduled relay edge.
2. Subtract the local pre-edge baseline.
3. Smooth only for peak finding with a centered rolling median, default `3 ms`.
4. Search for the dominant peak in the expected polarity:
   - `close`: positive peak
   - `open`: negative peak
5. Median-bin the post-peak tail, default `1 ms`.
6. Fit bounded single- and dual-exponential models with robust SciPy least squares.
7. Use BIC to decide whether the dual exponential is justified.

The dual-exponential model is:

```text
I(t) = offset + A * (f * exp(-t / tau_fast) + (1 - f) * exp(-t / tau_slow))
```

where `t = 0` at the fitted peak. For open edges the sign is handled internally, so fit amplitudes remain positive while the reported current and charge remain signed.

The fitted charge contribution from the exponential part is integrated analytically:

```text
Q_exp = A * [f * tau_fast * (1 - exp(-T / tau_fast))
       + (1 - f) * tau_slow * (1 - exp(-T / tau_slow))]
```

with sign applied for close/open direction. The fitted offset term contributes:

```text
Q_offset = offset * T
```

The pre-peak part of the edge window is integrated from the short rolling-median trace. This captures small current before the dominant peak without letting raw quantization dominate.

## BIC Model Selection

BIC means Bayesian Information Criterion. It compares models while penalizing extra parameters.

The single-exponential model has fewer parameters. The dual-exponential model is accepted only when its BIC is lower by at least `--bic-threshold`, default `10`:

```text
BIC_dual + threshold < BIC_single
```

This prevents the dual model from being accepted just because it has more freedom. In the 2026-05-22 Moku run, all 14 relay edges strongly supported the dual model.

## Whole-Run Charge Trace

The final modeled current trace is built by combining:

- median-binned current in flat regions
- fitted relay-edge current in edge windows

The cumulative signed charge is then computed by trapezoidal integration of the modeled current:

```text
Q_signed(t) = integral I_model(t) dt
```

The absolute transferred charge is also computed:

```text
Q_abs(t) = integral abs(I_model(t)) dt
```

This produces a full 750 s charge-transfer plot without directly integrating all high-rate noise.

## Outputs

For `moku_waveform.csv`, the script writes:

```text
moku_waveform_charge_transfer_timeseries.csv
moku_waveform_charge_transfer_edge_fits.csv
moku_waveform_charge_transfer.svg
moku_waveform_charge_transfer_edge_fits.svg
moku_waveform_charge_transfer_summary.md
```

For `oscilloscope_waveform.csv`, the same names are used with the `oscilloscope_waveform` stem.

The summary markdown gives whole-run charge totals and a compact per-edge table. The edge-fit CSV contains the fit parameters, BIC values, raw-window charge, median-window charge, and modeled-window charge.

## Charge Panel in the Waveform Analysis Plot

`scripts/plot_oscilloscope_waveform.py` (and therefore `plot-moku.sh`) shows a
charge panel in `*_analysis.svg` by reading `cumulative_charge_uC` from
`*_charge_transfer_timeseries.csv`. It does **not** integrate the raw CH2 current
itself: a naive cumulative integral accumulates the small whole-run current
offset (a ~0.16 uA offset over 750 s is ~0.12 mC, which dominates the real signed
charge and makes the trace ramp monotonically). The panel is shown only when the
timeseries exists, so run this analyzer first for the relay/step runs; sweep runs
have no relay edges and get no charge panel.

## Interpreting Current Results

For `2026-05-22_12-45-22_step_voltage_relay2_750s_moku`, the current workflow supports using Moku for charge-transfer analysis:

- All 14 relay edges accepted dual-exponential fits.
- Fast time constants were approximately `6.5-8.5 ms`.
- Slow time constants were approximately `69-96 ms`.
- Fitted edge-window charges closely tracked raw edge-window charges.
- Whole-run zero baseline was effectively `0 mA`.

The latest generated summary is:

```text
user-data/sessions/2026-05-22_12-45-22_step_voltage_relay2_750s_moku/moku_waveform_charge_transfer_summary.md
```

The oscilloscope sanity run on `2026-05-18_17-58-23_step_voltage_relay2_750s_oscilloscope` is less clean because that older run has a small zero-current offset and the first four low-voltage peaks fall below the current rejection threshold. It remains useful as an occasional validation path, but the 2026-05-22 Moku run is the cleaner current dataset for this analysis.

## Useful Options

```bash
# Change shunt or amplifier scaling
--shunt-ohms 330
--amplifier-gain 1

# Change edge fit region
--fit-end 1.0
--peak-search-end 0.6

# Change smoothing and binning
--edge-smooth-ms 3
--fit-bin-ms 1
--flat-bin-ms 200
--output-dt-ms 1

# Require stronger or weaker evidence for dual exponential
--bic-threshold 10
```
