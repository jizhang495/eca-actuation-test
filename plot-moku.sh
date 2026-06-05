#!/usr/bin/env bash
#
# plot-moku.sh — Plot the CH1/CH2 waveform from the most recent Moku session.
#
# Finds the newest moku_waveform.csv under the sessions directory (honouring
# ECA_DATA_DIR, like data_logger.py) and renders it with
# scripts/plot_oscilloscope_waveform.py, writing moku_waveform.svg and
# moku_waveform_analysis.svg beside the CSV.
#
# Extra arguments are forwarded to the plot script, e.g.:
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

# Pick the most recently modified moku_waveform.csv across all sessions.
matches="$(find "$SESSIONS_DIR" -maxdepth 2 -name moku_waveform.csv -printf '%T@\t%p\n' 2>/dev/null | sort -rn || true)"
if [[ -z "$matches" ]]; then
  echo "plot-moku.sh: no moku_waveform.csv found under $SESSIONS_DIR" >&2
  echo "Run a Moku session (measurement_source=moku) first." >&2
  exit 1
fi

first_line="${matches%%$'\n'*}"
csv="${first_line#*$'\t'}"
session_dir="$(dirname "$csv")"

echo "plot-moku.sh: plotting latest Moku waveform"
echo "  session: $(basename "$session_dir")"
echo "  csv:     $csv"

exec uv run python3 scripts/plot_oscilloscope_waveform.py "$csv" \
  --analysis-output "$session_dir/moku_waveform_analysis.svg" \
  "$@"
