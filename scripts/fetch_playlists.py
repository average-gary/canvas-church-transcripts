"""Map the channel's playlists onto the video index and flag sermons.

The channel mixes sermon series with class recordings, testimonies and one-off
events. A video counts as a sermon if it is in a sermon-series playlist, or --
because roughly forty sermons were never added to any playlist -- if it is not
in an excluded playlist and its title is not a known non-sermon event.

Videos can appear in more than one playlist (e.g. a series plus a seasonal
playlist), so `series` holds all matches and `series_primary` the first.

  python3 scripts/fetch_playlists.py
  python3 scripts/fetch_playlists.py --show-excluded
"""

import argparse
import json
import os
import re
import sys

from common import CHANNEL, DATA, load_index, run, save_index

# Playlists that are not sermons.
EXCLUDE_PLAYLIST = re.compile(r"^C3 Connect|^C3 Connection|^Testimonies$", re.I)

# One-off events and announcements that are not preaching. Full-service
# recordings ("Sunday @ Canvas ...") are deliberately NOT here: they contain the
# sermon, and worship segments reduce to [Music] cues that rendering drops.
NON_SERMON_TITLE = re.compile(
    r"night of worship"
    r"|christmas eve"
    r"|easter information"
    r"|leadership team address"
    r"|merger orientation"
    r"|testimony|testimonies"
    r"|bumper"          # short music-only series intro clips
    r"|^C3 Connect",
    re.I,
)

PLAYLISTS_JSON = DATA / "playlists.json"


def list_playlists():
    res = run([
        "yt-dlp", "--flat-playlist", "--no-warnings",
        "--print", "%(id)s\x1f%(title)s", f"{CHANNEL}/playlists",
    ])
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 2 and parts[0].startswith("PL"):
            out.append((parts[0], parts[1]))
    return out


def playlist_videos(pid):
    res = run([
        "yt-dlp", "--flat-playlist", "--no-warnings", "--ignore-errors",
        "--print", "%(id)s", f"https://www.youtube.com/playlist?list={pid}",
    ])
    return [v.strip() for v in res.stdout.splitlines() if len(v.strip()) == 11]


def fetch_meta(vid):
    cmd = [
        "yt-dlp", "--skip-download", "--no-warnings", "--simulate",
        "--print", "%(title)s\x1f%(duration)s\x1f%(upload_date)s",
        f"https://www.youtube.com/watch?v={vid}",
    ]
    browser = os.environ.get("YT_COOKIES_FROM_BROWSER")
    if browser:
        cmd[1:1] = ["--cookies-from-browser", browser]
    res = run(cmd)
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            title, dur, date = parts
            try:
                dur = int(float(dur))
            except ValueError:
                dur = None
            return title, dur, (date if re.fullmatch(r"\d{8}", date) else None)
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-excluded", action="store_true")
    args = ap.parse_args()

    idx = load_index()
    if not idx:
        sys.exit("index is empty - run fetch_index.py first")

    playlists = list_playlists()
    if not playlists:
        sys.exit("could not list playlists (rate limited? try again)")

    sermon_lists = [(p, t) for p, t in playlists if not EXCLUDE_PLAYLIST.search(t)]
    skipped = [(p, t) for p, t in playlists if EXCLUDE_PLAYLIST.search(t)]

    print(f"{len(playlists)} playlists: {len(sermon_lists)} sermon, {len(skipped)} excluded")
    for _, t in skipped:
        print(f"  excluded playlist: {t}")
    print()

    # Reset derived fields so re-runs reflect the current rules.
    for e in idx.values():
        e["series"] = []
        e["excluded_playlist"] = False

    mapping, unknown = {}, set()
    for n, (pid, title) in enumerate(sermon_lists, 1):
        vids = playlist_videos(pid)
        mapping[title] = vids
        for v in vids:
            if v not in idx:
                unknown.add(v)
                continue
            idx[v]["series"].append(title)
        print(f"  [{n}/{len(sermon_lists)}] {title[:52]}: {len(vids)}")

    # Videos reachable only through a playlist (often unlisted) get a stub entry.
    for v in sorted(unknown):
        idx.setdefault(v, {"id": v, "title": v, "duration": None, "captions": None,
                           "series": ["(playlist only)"], "tab": "playlist"})
        idx[v].setdefault("excluded_playlist", False)

    # Backfill metadata for any entry still missing a real title, duration or
    # date -- including stubs left behind by an earlier run. Flat-playlist
    # extraction never returns upload_date, so this is the only source for it.
    for v, e in sorted(idx.items()):
        if e.get("title") and e["title"] != v and e.get("duration") and e.get("upload_date"):
            continue
        title, dur, date = fetch_meta(v)
        if title:
            e["title"] = title
        if dur:
            e["duration"] = dur
        if date:
            e["upload_date"] = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        print(f"  backfilled metadata: {v}  {(title or '?')[:50]}")

    # Mark membership in excluded playlists so title rules can't override them.
    for pid, title in skipped:
        for v in playlist_videos(pid):
            if v in idx:
                idx[v]["excluded_playlist"] = True

    for e in idx.values():
        title = e.get("title") or ""
        in_series = bool(e["series"])
        # Title rules outrank playlist membership: testimonies and one-off
        # events do turn up inside sermon-series playlists.
        if e.get("excluded_playlist"):
            e["sermon"], e["sermon_reason"] = False, "in excluded playlist"
        elif NON_SERMON_TITLE.search(title):
            e["sermon"], e["sermon_reason"] = False, "non-sermon title"
        elif in_series:
            e["sermon"], e["sermon_reason"] = True, "in sermon playlist"
        else:
            e["sermon"], e["sermon_reason"] = True, "standalone sermon"
        e["series_primary"] = e["series"][0] if e["series"] else None

    PLAYLISTS_JSON.write_text(json.dumps(
        {"sermon_playlists": mapping, "excluded_playlists": [t for _, t in skipped]},
        indent=2,
    ))
    save_index(idx)

    sermons = [e for e in idx.values() if e["sermon"]]
    hours = sum(e.get("duration") or 0 for e in sermons) / 3600
    reasons = {}
    for e in idx.values():
        key = ("sermon: " if e["sermon"] else "skip: ") + e["sermon_reason"]
        reasons[key] = reasons.get(key, 0) + 1
    print(f"\nsermons: {len(sermons)} videos, {hours:.1f} hours")
    for k, v in sorted(reasons.items()):
        print(f"  {k}: {v}")

    if args.show_excluded:
        print("\nexcluded:")
        for v, e in sorted(idx.items(), key=lambda kv: kv[1].get("title", "")):
            if not e["sermon"]:
                print(f"  {v}  {e.get('title', '')[:58]}  ({e['sermon_reason']})")


if __name__ == "__main__":
    main()
