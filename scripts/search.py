"""Full-text search across every sermon transcript.

Builds a SQLite FTS5 index over paragraph-sized chunks, so a hit points at a
timestamp you can click, not just at a sermon. The index rebuilds from the
transcription artifacts, so it is disposable.

  python3 scripts/search.py --build
  python3 scripts/search.py "kingdom of God"
  python3 scripts/search.py "NEAR(grace faith, 6)" --limit 30
  python3 scripts/search.py "prodigal" --series JONAH
  python3 scripts/search.py "fasting" --json

FTS5 query syntax works: "quoted phrases", AND / OR / NOT, prefix*, and
proximity as NEAR(term1 term2, n) -- note the parenthesised form.
"""

import argparse
import json
import sqlite3
import sys

from common import DATA, load_index, paragraphs
from render import cues_for

DB = DATA / "search.db"


def build(verbose=True):
    idx = load_index()
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
        CREATE TABLE sermon (
            video_id TEXT PRIMARY KEY, title TEXT, date TEXT,
            series TEXT, duration INTEGER, source TEXT, words INTEGER
        );
        CREATE VIRTUAL TABLE chunk USING fts5(
            body, video_id UNINDEXED, start UNINDEXED,
            tokenize = "porter unicode61"
        );
    """)

    sermons = chunks = 0
    for vid, e in sorted(idx.items(), key=lambda kv: kv[1].get("upload_date") or ""):
        if not e.get("sermon"):
            continue
        cues, source = cues_for(vid)
        if not cues:
            continue
        paras = paragraphs(cues)
        con.execute(
            "INSERT INTO sermon VALUES (?,?,?,?,?,?,?)",
            (vid, e["title"], e.get("upload_date") or "unknown",
             e.get("series_primary") or "(standalone)", e.get("duration") or 0,
             source, sum(len(c[-1].split()) for c in cues)),
        )
        con.executemany(
            "INSERT INTO chunk (body, video_id, start) VALUES (?,?,?)",
            [(text, vid, start) for start, text in paras],
        )
        sermons += 1
        chunks += len(paras)

    con.commit()
    con.close()
    if verbose:
        print(f"indexed {sermons} sermons, {chunks:,} passages -> {DB}")
    return sermons


def search(query, limit=15, series=None):
    if not DB.exists():
        build(verbose=False)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT s.title, s.date, s.series, c.video_id, c.start,
               snippet(chunk, 0, '<<', '>>', ' ... ', 24) AS snip,
               bm25(chunk) AS score
        FROM chunk c JOIN sermon s ON s.video_id = c.video_id
        WHERE chunk MATCH ?
    """
    params = [query]
    if series:
        sql += " AND s.series LIKE ?"
        params.append(f"%{series}%")
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        rows = [dict(r) for r in con.execute(sql, params)]
    except sqlite3.OperationalError as exc:
        sys.exit(f"bad query: {exc}")
    finally:
        con.close()
    for r in rows:
        r["url"] = f"https://www.youtube.com/watch?v={r['video_id']}&t={int(r['start'])}s"
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--series")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.build:
        build()
        if not args.query:
            return
    if not args.query:
        ap.error("give a query, or --build")

    rows = search(args.query, args.limit, args.series)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no matches")
        return

    print(f"{len(rows)} matches for {args.query!r}\n")
    for r in rows:
        h, rem = divmod(int(r["start"]), 3600)
        stamp = f"{h}:{rem // 60:02d}:{rem % 60:02d}"
        print(f"  {r['date']}  {r['title'][:56]}")
        print(f"  {r['series'][:56]}  @ {stamp}")
        print(f"  {r['snip']}")
        print(f"  {r['url']}\n")


if __name__ == "__main__":
    main()
