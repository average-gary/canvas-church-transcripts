#!/usr/bin/env bash
# Full pipeline for the Canvas Winchester sermon archive.
#
# Every stage is resumable and skips work already on disk, so re-running after
# an interruption (or after new sermons are posted) is safe and cheap.
#
#   ./run.sh              # all stages
#   ./run.sh transcribe   # one stage
#   ./run.sh render search
#
# YouTube rate-limits aggressively; cookies avoid the "confirm you're not a bot"
# wall. Override the browser with YT_COOKIES_FROM_BROWSER=brave, etc.

set -euo pipefail
cd "$(dirname "$0")"

export YT_COOKIES_FROM_BROWSER="${YT_COOKIES_FROM_BROWSER:-chrome}"

STAGES=("$@")
if [ ${#STAGES[@]} -eq 0 ]; then
  STAGES=(index playlists transcribe render summarize render search)
fi

for stage in "${STAGES[@]}"; do
  echo "==> $stage"
  case "$stage" in
    index)      python3 -u scripts/fetch_index.py ;;
    playlists)  python3 -u scripts/fetch_playlists.py ;;
    captions)   python3 -u scripts/fetch_captions.py ;;   # optional fast fallback
    transcribe) python3 -u scripts/transcribe.py ;;
    render)     python3 -u scripts/render.py ;;
    summarize)  python3 -u scripts/summarize.py ;;
    search)     python3 -u scripts/search.py --build ;;
    *) echo "unknown stage: $stage" >&2; exit 2 ;;
  esac
  echo
done

echo "done. try: python3 scripts/search.py \"the kingdom of God\""
