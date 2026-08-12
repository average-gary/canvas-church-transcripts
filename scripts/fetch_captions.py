"""Download YouTube auto-captions for indexed videos.

YouTube rate-limits hard (HTTP 429 -> "confirm you're not a bot"), so this runs
serially with a delay between videos and backs off when throttled. It records
three distinct states, because conflating them silently wastes GPU hours on
videos that actually do have captions:

  True  - captions downloaded and on disk
  False - YouTube explicitly reports no automatic captions for this video
  None  - unknown: request was blocked/errored, so retry later

Set YT_COOKIES_FROM_BROWSER (e.g. "chrome", "brave", "firefox") to authenticate
if throttling persists.

  python3 scripts/fetch_captions.py                # fill in unknowns + retries
  python3 scripts/fetch_captions.py --limit 10     # small probe
  python3 scripts/fetch_captions.py --delay 6      # go slower
"""

import argparse
import os
import re
import sys
import time

from common import CAPTIONS, load_index, run, save_index

BLOCKED = re.compile(r"429|too many requests|not a bot|sign in to confirm", re.I)
# Only an explicit statement from yt-dlp counts as "no captions"; anything else
# stays unknown so it gets retried instead of burning GPU time on whisper.
NO_CAPS = re.compile(r"has no automatic captions", re.I)
UNAVAILABLE = re.compile(r"video unavailable|private video|removed by the uploader|members-only", re.I)


def caption_file(vid):
    """Preferred caption track on disk, if any. en-orig beats the en re-render."""
    for suffix in ("en-orig", "en"):
        p = CAPTIONS / f"{vid}.{suffix}.json3"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def fetch(vid):
    """Return (state, upload_date, note). state is True / False / None."""
    cmd = [
        "yt-dlp", "--skip-download", "--no-warnings",
        "--write-auto-subs", "--sub-langs", "en-orig,en", "--sub-format", "json3",
        "--sleep-requests", "1.5", "--retries", "3",
        # --print implies --simulate, which silently suppresses writing the
        # subtitle files. --no-simulate restores the download.
        "--print", "%(upload_date)s", "--no-simulate",
        "-o", str(CAPTIONS / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={vid}",
    ]
    browser = os.environ.get("YT_COOKIES_FROM_BROWSER")
    if browser:
        cmd[1:1] = ["--cookies-from-browser", browser]

    res = run(cmd)
    blob = f"{res.stdout}\n{res.stderr}"

    date = None
    for line in res.stdout.splitlines():
        if re.fullmatch(r"\d{8}", line.strip()):
            date = line.strip()
            break

    if caption_file(vid):
        return True, date, "ok"
    if BLOCKED.search(blob):
        return None, date, "blocked"
    if UNAVAILABLE.search(blob):
        return False, date, "unavailable"
    if NO_CAPS.search(blob):
        return False, date, "no-captions"
    tail = blob.strip().splitlines()[-1][:110] if blob.strip() else ""
    return None, date, f"error: {tail}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between videos")
    ap.add_argument("--recheck-all", action="store_true", help="ignore cached states")
    args = ap.parse_args()

    idx = load_index()
    if not idx:
        sys.exit("index is empty - run fetch_index.py first")

    def needs_work(vid, e):
        if caption_file(vid):
            return False
        return args.recheck_all or e.get("captions") is None

    todo = [v for v, e in sorted(idx.items()) if needs_work(v, e)]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to fetch")
        return

    print(f"{len(todo)} videos to check, {args.delay}s apart\n")
    counts, backoff, consecutive_blocks = {}, args.delay, 0

    for n, vid in enumerate(todo, 1):
        state, date, note = fetch(vid)
        counts[note.split(":")[0]] = counts.get(note.split(":")[0], 0) + 1
        idx[vid]["captions"] = state
        if date:
            idx[vid]["upload_date"] = f"{date[:4]}-{date[4:6]}-{date[6:]}"

        if note == "blocked":
            consecutive_blocks += 1
            backoff = min(backoff * 2, 300)
            print(f"  [{n}/{len(todo)}] {vid} BLOCKED - backing off {backoff:.0f}s", flush=True)
            if consecutive_blocks >= 5:
                print("\n!! persistently rate-limited; stopping. Re-run later, "
                      "or set YT_COOKIES_FROM_BROWSER to authenticate.", flush=True)
                break
            time.sleep(backoff)
            continue

        consecutive_blocks, backoff = 0, args.delay
        if n % 10 == 0 or n == len(todo):
            print(f"  [{n}/{len(todo)}] {counts}", flush=True)
            save_index(idx)
        time.sleep(args.delay)

    save_index(idx)
    have = sum(1 for v in idx if caption_file(v))
    none = sum(1 for e in idx.values() if e.get("captions") is False)
    unknown = len(idx) - have - none
    gap_h = sum(e.get("duration") or 0 for v, e in idx.items()
                if e.get("captions") is False) / 3600
    print(f"\ncaptions on disk: {have}/{len(idx)}")
    print(f"confirmed no captions: {none}  ({gap_h:.1f}h -> whisper)")
    print(f"still unknown: {unknown}  (re-run to retry)")


if __name__ == "__main__":
    main()
