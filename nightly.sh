#!/usr/bin/env bash
# Transcribe the remaining sermons, then finish the archive unattended.
#
# Starts gentle so the machine stays usable, and takes the extra cores once the
# ramp time passes. Every stage is resumable, so killing this and re-running it
# costs only the sermon that was in flight.
#
#   ./nightly.sh                  # gentle now, full speed at 18:00
#   RAMP_AT=21:00 ./nightly.sh    # ramp later
#   RAMP_AT= ./nightly.sh         # no ramp, stay gentle throughout
#
# caffeinate keeps the Mac awake while this runs. Note it cannot defeat a closed
# lid on battery -- leave the lid open, or plugged in with an external display.

set -uo pipefail
cd "$(dirname "$0")"

export YT_COOKIES_FROM_BROWSER="${YT_COOKIES_FROM_BROWSER:-chrome}"

DAY_THREADS="${DAY_THREADS:-4}"      # 4 of 16 cores; leaves 12 for you
DAY_NICE="${DAY_NICE:-10}"           # yields to anything you're doing
RAMP_THREADS="${RAMP_THREADS:-10}"
RAMP_AT="${RAMP_AT-18:00}"

mkdir -p logs
LOG="logs/nightly.log"

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

say "starting: ${DAY_THREADS} threads (nice ${DAY_NICE})${RAMP_AT:+, ramp to ${RAMP_THREADS} at ${RAMP_AT}}"

ramp_args=()
[ -n "$RAMP_AT" ] && ramp_args=(--ramp-at "$RAMP_AT" --ramp-threads "$RAMP_THREADS" --ramp-nice 0)

# -i prevents idle sleep; -s keeps the system awake while on AC power.
caffeinate -is python3 -u scripts/transcribe.py \
  --threads "$DAY_THREADS" --nice "$DAY_NICE" "${ramp_args[@]}" \
  2>&1 | tee -a "$LOG"

status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  say "transcription exited $status - stopping before the follow-on stages."
  say "re-run ./nightly.sh to resume; nothing already transcribed is lost."
  exit "$status"
fi

say "transcription complete - rendering"
caffeinate -is python3 -u scripts/render.py 2>&1 | tee -a "$LOG"

say "summarising (claude CLI, one call per sermon)"
caffeinate -is python3 -u scripts/summarize.py 2>&1 | tee -a "$LOG"

say "re-rendering with summaries folded in"
caffeinate -is python3 -u scripts/render.py 2>&1 | tee -a "$LOG"

say "building search index"
python3 -u scripts/search.py --build 2>&1 | tee -a "$LOG"

say "archive complete. try: python3 scripts/search.py \"the kingdom of God\""
