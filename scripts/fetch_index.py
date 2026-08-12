"""Build data/index.json: every video on the channel with metadata.

Flat-playlist extraction is one request per tab, so this is cheap. It does not
return upload_date, which gets filled in later by fetch_captions.py from the
per-video metadata it already has to pull.
"""

import sys

from common import CHANNEL, load_index, run, save_index

TABS = ("streams", "videos")
FIELDS = "%(id)s\x1f%(title)s\x1f%(duration)s"


def main():
    idx = load_index()
    found = 0
    for tab in TABS:
        res = run([
            "yt-dlp", "--flat-playlist", "--ignore-errors",
            "--print", FIELDS, f"{CHANNEL}/{tab}",
        ])
        if res.returncode != 0 and not res.stdout.strip():
            print(f"  !! {tab}: {res.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
            continue
        for line in res.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            vid, title, dur = parts
            found += 1
            entry = idx.setdefault(vid, {"id": vid})
            entry["title"] = title
            entry["tab"] = tab
            try:
                entry["duration"] = int(float(dur))
            except ValueError:
                entry["duration"] = None
        print(f"  {tab}: {found} cumulative")

    save_index(idx)
    total = sum(e.get("duration") or 0 for e in idx.values())
    print(f"\n{len(idx)} videos indexed, {total / 3600:.1f} hours total")


if __name__ == "__main__":
    main()
