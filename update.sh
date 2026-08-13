#!/usr/bin/env bash
# Poll for new sermons, ingest whatever turned up, rebuild and commit.
#
# Safe to run on a schedule: every stage below already skips work it has done, so
# a run with nothing new costs ~40 YouTube requests and no writes.
#
#   ./update.sh              # ingest and commit locally
#   ./update.sh --push       # ...and push, which publishes to GitHub Pages
#   ./update.sh --dry-run    # poll and report, change nothing
#
# Exit 0 with "nothing new" is the normal outcome most days.

set -euo pipefail
cd "$(dirname "$0")"

PUSH=0
DRY=0
for a in "$@"; do
  case "$a" in
    --push) PUSH=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

[ -f data/index.json ] || { echo "no data/index.json - run ./run.sh first" >&2; exit 1; }

# Sermon ids, for reporting which ones are new.
sermon_ids() {
  python3 -c "
import json
idx = json.load(open('data/index.json'))
print('\n'.join(sorted(v for v, e in idx.items() if e.get('sermon'))))"
}

before_ids=$(sermon_ids)
before_hash=$(shasum -a 256 data/index.json | cut -d" " -f1)

echo "==> polling the channel"
# Polling writes both of these, so a dry run keeps copies and puts them back.
if [ "$DRY" = 1 ]; then
  for f in data/index.json data/playlists.json; do
    [ -f "$f" ] && cp "$f" "$f.pre-dry-run"
  done
fi
./run.sh index playlists

after_hash=$(shasum -a 256 data/index.json | cut -d" " -f1)
new_ids=$(comm -13 <(echo "$before_ids") <(sermon_ids) || true)

if [ "$DRY" = 1 ]; then
  for f in data/index.json data/playlists.json; do
    [ -f "$f.pre-dry-run" ] && mv "$f.pre-dry-run" "$f"
  done
fi

# The index is the source of truth for everything downstream, so its hash is the
# gate: it changes for a new video, a new series assignment on an old one, or a
# backfilled date. Any of those wants a rebuild; none of them, and we are done.
if [ "$before_hash" = "$after_hash" ]; then
  echo
  echo "nothing new. index unchanged ($(echo "$before_ids" | wc -l | tr -d ' ') sermons)"
  exit 0
fi

echo
if [ -n "$new_ids" ]; then
  echo "new sermons:"
  while read -r v; do
    [ -n "$v" ] && python3 -c "
import json, sys
e = json.load(open('data/index.json'))['$v']
print(f\"  {e.get('upload_date') or '????-??-??'}  {e.get('title', '')[:60]}\")"
  done <<< "$new_ids"
else
  echo "no new sermons, but the index changed - a series or date was updated"
fi

if [ "$DRY" = 1 ]; then
  echo
  echo "--dry-run: index.json restored, nothing else touched"
  exit 0
fi

echo
echo "==> ingesting"
# Called directly rather than through run.sh so an unattended run can yield the
# CPU: a scheduled job should not peg eight whisper threads while someone works.
python3 -u scripts/transcribe.py ${TRANSCRIBE_ARGS:---nice 10}
./run.sh render summarize render search
./site.sh

if [ -z "$(git status --porcelain)" ]; then
  echo "nothing to commit"
  exit 0
fi

count=$(echo "$new_ids" | grep -c . || true)
if [ "$count" -gt 0 ]; then
  subject="Add $count new sermon$([ "$count" = 1 ] || echo s)"
else
  subject="Update sermon metadata"
fi

git add -A
git commit -q -m "$subject

$(if [ -n "$new_ids" ]; then
    while read -r v; do
      [ -n "$v" ] && python3 -c "
import json
e = json.load(open('data/index.json'))['$v']
print(f\"- {e.get('upload_date')}  {e.get('title', '')}\")"
    done <<< "$new_ids"
  else
    echo "Series assignments or dates changed upstream; no new recordings."
  fi)"
echo "committed: $subject"

if [ "$PUSH" = 1 ]; then
  git push -q origin main
  echo "pushed - GitHub Pages will rebuild within a minute or two"
else
  echo "not pushed. ./update.sh --push to publish, or push by hand"
fi
