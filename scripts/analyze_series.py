"""Look for series hiding among the sermons nobody playlisted.

45 sermons are marked standalone, but almost all of them predate the channel's
use of playlists - so "standalone" often means "nobody made a playlist", not
"preached on its own". This reports candidates with their evidence and changes
nothing; series assignment stays a human call.

Passes, most useful first:

  0. What the preacher said. Series get announced from the platform - "we're
     continuing our series through the book of James". First-hand evidence,
     so it settles anything the inference passes below only suggest.
  1. Contiguous standalone blocks. A run of consecutive sermons where every one
     lacks a series is the strongest signal there is - a series was preached and
     never playlisted.
  2. Scripture clusters. Sermons close in time whose summaries keep citing the
     same book. When a cluster also contains sermons from an existing series,
     the standalones in it probably belong to that series.
  3. Title patterns. Shared prefixes, and prefixes matching a known series name.

  python3 scripts/analyze_series.py
  python3 scripts/analyze_series.py --gap 28 --min-run 3
"""

import argparse
import re
from collections import Counter, defaultdict
from datetime import date

from build_site import book_of
from common import ROOT, load_index, load_summary, transcript_path

STOP = {"the", "a", "of", "and", "to", "in", "is", "for", "our", "we", "you",
        "your", "my", "on", "at", "with", "god", "jesus", "christ", "lord",
        "church", "sunday", "canvas", "part", "week"}


def as_date(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def load():
    idx = load_index()
    out = []
    for vid, e in idx.items():
        if not e.get("sermon") or not e.get("upload_date"):
            continue
        sm = load_summary(vid)
        books = Counter(b for b in (book_of(r) for r in (sm.get("scriptures") or [])) if b)
        out.append({
            "id": vid,
            "title": e.get("title") or vid,
            "date": e["upload_date"],
            "d": as_date(e["upload_date"]),
            "series": e.get("series") or [],
            "books": books,
            "top": [b for b, _ in books.most_common(2)],
            "themes": set(sm.get("themes") or []),
        })
    out.sort(key=lambda s: s["d"])
    return out


def tag(s):
    return f'{s["date"]}  {s["title"][:52]}'


def common_books(members, least=2):
    """Books cited by at least `least` members, most widely shared first."""
    seen = Counter()
    for m in members:
        for b in m["books"]:
            seen[b] += 1
    return [(b, n) for b, n in seen.most_common() if n >= least]


# ------------------------------------------------- 0. what the preacher said

# Series are announced from the platform: "we're continuing our series through
# the book of James", "launching this new series through the fruit of the
# Spirit". That is first-hand evidence and beats anything inferred from dates or
# scripture counts, so it runs first.
SAID = re.compile(
    r"[^.?!]{0,110}\b(?:"
    r"(?:new |this |our |the |a )?(?:sermon )?series\b"
    r"|week (?:one|two|three|four|five|six|seven|\d+) of\b"
    r"|part (?:one|two|three|four|\d+) of\b"
    r")[^.?!]{0,110}[.?!]", re.I)
# "watching the Chosen series", "a series of unfortunate events" - not a series.
NOT_OURS = re.compile(r"\b(?:watching|netflix|tv|chosen series|world series)\b", re.I)
INTRO_CHARS = 11000     # series framing lives in the opening minutes


def spoken_series(sermon):
    md = transcript_path({"upload_date": sermon["date"], "title": sermon["title"],
                          "id": sermon["id"]})
    txt = ROOT / "text" / f"{md.stem}.txt"
    if not txt.exists():
        return []
    head = txt.read_text()[:INTRO_CHARS]
    out = []
    for m in SAID.finditer(head):
        line = re.sub(r"\s+", " ", m.group()).strip()
        if NOT_OURS.search(line) or len(line) < 25:
            continue
        out.append(line)
    return out


def report_spoken(sermons):
    print(f"\n{'=' * 78}\n0. WHAT THE PREACHER SAID\n{'=' * 78}")
    print("  Series announcements quoted from the opening minutes of standalone\n"
          "  sermons. This is first-hand evidence; everything below is inference.")
    found = 0
    for s in sermons:
        if s["series"]:
            continue
        lines = spoken_series(s)
        if not lines:
            continue
        found += 1
        print(f"\n  {tag(s)}")
        for line in lines[:3]:
            print(f"     “{line[:132]}”")
    print(f"\n  {found} of the standalone sermons name a series out loud.")


# --------------------------------------------------- 1. standalone-only blocks

def standalone_blocks(sermons, gap, min_run):
    """Runs of consecutive sermons in which none has a series."""
    blocks, cur = [], []
    for s in sermons:
        solo = not s["series"]
        near = not cur or (s["d"] - cur[-1]["d"]).days <= gap
        if solo and near:
            cur.append(s)
            continue
        if len(cur) >= min_run:
            blocks.append(cur)
        cur = [s] if solo else []
    if len(cur) >= min_run:
        blocks.append(cur)
    return blocks


def report_blocks(blocks):
    print(f"\n{'=' * 78}\n1. CONTIGUOUS STANDALONE BLOCKS\n{'=' * 78}")
    if not blocks:
        print("  none")
        return
    print(f"  {len(blocks)} runs of consecutive sermons where nothing has a series.")
    for b in blocks:
        span = (b[-1]["d"] - b[0]["d"]).days
        print(f"\n  -- {len(b)} sermons, {b[0]['date']} -> {b[-1]['date']} ({span} days)")
        shared = common_books(b, least=max(2, len(b) // 3))
        if shared:
            print(f"     shared scripture: "
                  + ", ".join(f"{bk} in {n}/{len(b)}" for bk, n in shared[:5]))
        th = Counter(t for m in b for t in m["themes"])
        rep = [t for t, n in th.most_common(6) if n >= 2]
        if rep:
            print(f"     recurring themes: {', '.join(rep)}")
        for m in b:
            print(f"       {tag(m)}")


# ------------------------------------------------------ 2. scripture clusters

def scripture_clusters(sermons, gap, min_run):
    """Sermons close in time that keep citing the same book."""
    out = []
    for book in {b for s in sermons for b in s["top"]}:
        hits = [s for s in sermons if book in s["top"]]
        cluster = []
        for s in hits:
            if cluster and (s["d"] - cluster[-1]["d"]).days > gap * 2:
                out.append((book, cluster))
                cluster = []
            cluster.append(s)
        out.append((book, cluster))

    keep = []
    for book, members in out:
        solo = [m for m in members if not m["series"]]
        if len(members) >= min_run and len(solo) >= 2:
            keep.append((len(solo), len(members), book, members))
    keep.sort(reverse=True, key=lambda k: (k[0], k[1]))
    return keep


def report_clusters(clusters):
    print(f"\n{'=' * 78}\n2. SCRIPTURE CLUSTERS\n{'=' * 78}")
    if not clusters:
        print("  none")
        return
    print("  Sermons near each other in time whose summaries lean on the same book.\n"
          "  Where a cluster also holds sermons from a real series, the standalones\n"
          "  in it are the ones worth a second look.")
    for n_solo, n_all, book, members in clusters:
        existing = Counter(x for m in members for x in m["series"])
        print(f"\n  -- {book}: {n_all} sermons, {n_solo} of them standalone "
              f"({members[0]['date']} -> {members[-1]['date']})")
        if existing:
            print("     overlaps series: "
                  + ", ".join(f"{s} ({n})" for s, n in existing.most_common(3)))
        for m in members:
            mark = "  *" if not m["series"] else "   "
            where = "" if not m["series"] else f"   [{m['series'][0]}]"
            print(f"     {mark} {tag(m)}{where}")
    print("\n  (* = currently standalone)")


# --------------------------------------------------------- 3. title patterns

def title_key(t):
    """Series-ish prefix of a title: the bit before a colon, or first 2 words."""
    t = re.sub(r"\s+", " ", t).strip()
    if ":" in t:
        return t.split(":", 1)[0].strip().lower()
    words = [w for w in re.findall(r"[A-Za-z']+", t.lower()) if w not in STOP]
    return " ".join(words[:2])


def report_titles(sermons):
    print(f"\n{'=' * 78}\n3. TITLE PATTERNS\n{'=' * 78}")
    known = {}
    for s in sermons:
        for name in s["series"]:
            known[name.lower()] = name

    solo = [s for s in sermons if not s["series"]]
    hits = 0
    for s in solo:
        key = title_key(s["title"])
        if key and key in known:
            print(f"  prefix matches series {known[key]!r}:  {tag(s)}")
            hits += 1

    groups = defaultdict(list)
    for s in solo:
        k = title_key(s["title"])
        if k:
            groups[k].append(s)
    for k, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 2:
            continue
        hits += 1
        print(f"\n  {len(items)} standalone sermons share the prefix {k!r}:")
        for m in items:
            print(f"       {tag(m)}")
    if not hits:
        print("  nothing")


# ---------------------------------------------------------------- neighbours

def report_neighbours(sermons, gap):
    """Standalones sitting inside or beside a series' run of weeks."""
    print(f"\n{'=' * 78}\n4. STANDALONES NEXT TO A SERIES\n{'=' * 78}")
    print("  A standalone whose neighbouring weeks are all one series is either a\n"
          "  guest week or a missed playlist entry. Scripture overlap tells them apart.")
    shown = 0
    for i, s in enumerate(sermons):
        if s["series"]:
            continue
        before = next((x for x in reversed(sermons[:i]) if x["series"]), None)
        after = next((x for x in sermons[i + 1:] if x["series"]), None)
        if not (before and after):
            continue
        same = set(before["series"]) & set(after["series"])
        if not same:
            continue
        d_b, d_a = (s["d"] - before["d"]).days, (after["d"] - s["d"]).days
        if d_b > gap or d_a > gap:
            continue
        name = sorted(same)[0]
        pool = Counter()
        for x in sermons:
            if name in x["series"]:
                pool += x["books"]
        overlap = [b for b in s["top"] if b in pool]
        shown += 1
        print(f"\n  {tag(s)}")
        print(f"     sits {d_b}d after and {d_a}d before {name!r}")
        print(f"     its books: {', '.join(s['top']) or 'none'}"
              f"   series' top books: {', '.join(b for b, _ in pool.most_common(3))}")
        print(f"     -> {'shares ' + ', '.join(overlap) if overlap else 'no scripture overlap'}")
    if not shown:
        print("\n  none")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=int, default=28,
                    help="days between sermons still counted as consecutive")
    ap.add_argument("--min-run", type=int, default=3)
    args = ap.parse_args()

    sermons = load()
    solo = [s for s in sermons if not s["series"]]
    print(f"{len(sermons)} sermons, {len(solo)} standalone "
          f"({sermons[0]['date']} -> {sermons[-1]['date']})")
    no_refs = [s for s in solo if not s["books"]]
    if no_refs:
        print(f"note: {len(no_refs)} standalone sermons have no usable scripture refs, "
              f"so passes 2 and 4 can't see them")

    report_spoken(sermons)
    report_blocks(standalone_blocks(sermons, args.gap, args.min_run))
    report_clusters(scripture_clusters(sermons, args.gap, args.min_run))
    report_titles(sermons)
    report_neighbours(sermons, args.gap)
    print("\nNothing was changed. To act on a proposal, add the video id and series\n"
          "name to data/series_manual.json, then re-run:\n"
          "  python3 scripts/render.py && ./site.sh")


if __name__ == "__main__":
    main()
