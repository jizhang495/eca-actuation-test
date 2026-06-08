#!/usr/bin/env bash
#
# plot-moku.sh — Plot CH1/CH2 waveforms for recent Moku sessions.
#
# Walks the session folders newest-first (their names start with a zero-padded
# 2026-06-05_HH-MM-SS timestamp, so a reverse string sort is chronological) and
# plots every Moku session (one that has moku_waveform.csv) that has not been
# plotted yet. It stops as soon as it reaches a session that already has a plot
# (moku_waveform.svg), or once it has gone through every folder. Non-Moku
# sessions (no moku_waveform.csv) are skipped without stopping the walk.
#
# So a re-run only plots sessions recorded since the last run.
#
# Each plotted session gets moku_waveform.svg and moku_waveform_analysis.svg
# written beside its CSV, via scripts/plot_oscilloscope_waveform.py. Extra
# arguments are forwarded to that plot script, e.g.:
#   ./plot-moku.sh --shunt-ohms 100 --x-min 0 --x-max 5
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Resolve the sessions directory the same way data_logger.py does.
if [[ -n "${ECA_DATA_DIR:-}" ]]; then
  case "$ECA_DATA_DIR" in
    /*) SESSIONS_DIR="$ECA_DATA_DIR" ;;
    *)  SESSIONS_DIR="$REPO_ROOT/$ECA_DATA_DIR" ;;
  esac
else
  SESSIONS_DIR="$REPO_ROOT/user-data/sessions"
fi

# Collect session folders, then order them newest-first by name.
shopt -s nullglob
session_dirs=("$SESSIONS_DIR"/*/)
shopt -u nullglob
if [[ ${#session_dirs[@]} -eq 0 ]]; then
  echo "plot-moku.sh: no session folders under $SESSIONS_DIR" >&2
  exit 1
fi
mapfile -t session_dirs < <(printf '%s\n' "${session_dirs[@]}" | sort -r)

plotted=0
failed=0
for dir in "${session_dirs[@]}"; do
  dir="${dir%/}"
  name="$(basename "$dir")"

  if [[ -f "$dir/moku_waveform.svg" ]]; then
    echo "plot-moku.sh: reached already-plotted session $name; stopping."
    break
  fi

  # No plot yet: plot it if it is a Moku session, otherwise skip and keep going.
  [[ -f "$dir/moku_waveform.csv" ]] || continue

  echo "plot-moku.sh: plotting $name"
  if uv run python3 scripts/plot_oscilloscope_waveform.py "$dir/moku_waveform.csv" \
      --analysis-output "$dir/moku_waveform_analysis.svg" "$@"; then
    plotted=$((plotted + 1))
  else
    echo "plot-moku.sh: WARNING failed to plot $name" >&2
    failed=$((failed + 1))
  fi
done

echo "plot-moku.sh: plotted $plotted session(s), $failed failure(s)."
[[ $failed -eq 0 ]]
