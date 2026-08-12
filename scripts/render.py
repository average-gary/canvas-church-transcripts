"""Turn transcription artifacts into readable transcripts.

Emits, per sermon:
  transcripts/<date>-<slug>-<id>.md    timestamped, links back to the video
  text/<date>-<slug>-<id>.txt          clean prose, no timestamps
  srt/<date>-<slug>-<id>.srt           subtitles

Prefers a local whisper transcription (punctuated, cased) and falls back to
YouTube auto-captions. Rebuilds from artifacts on every run, so it is cheap to
re-run after changing formatting in common.py.

  python3 scripts/render.py
  python3 scripts/render.py --no-txt --no-srt
  python3 scripts/render.py --include-non-sermons
"""

import argparse
import json

from common import (
    DATA,
    ROOT,
    TRANSCRIPTS,
    hhmmss,
    load_index,
    paragraphs,
    parse_json3,
    parse_whisper_json,
    render_markdown,
    slugify,
    transcript_path,
)
from fetch_captions import caption_file

WHISPER_JSON = DATA / "whisper"
TEXT_DIR = ROOT / "text"
SRT_DIR = ROOT / "srt"

# Below this, a "transcript" is almost certainly music or silence, not preaching.
MIN_WORDS = 200


def cues_for(vid):
    """Best available artifact for a video: (cues, source_label)."""
    wj = WHISPER_JSON / f"{vid}.json"
    if wj.exists() and wj.stat().st_size > 0:
        return parse_whisper_json(wj), "whisper-large-v3-turbo"
    cf = caption_file(vid)
    if cf:
        return parse_json3(cf), "youtube-auto-captions"
    return None, None


def srt_time(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path, cues):
    out = []
    for n, (start, end, text) in enumerate(cues, 1):
        if end <= start:
            end = start + 2.0
        out.append(f"{n}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    path.write_text("\n".join(out))


def write_txt(path, entry, cues):
    body = "\n\n".join(text for _, text in paragraphs(cues))
    header = f"{entry['title']}\n{entry.get('upload_date') or 'unknown date'}\n"
    header += f"https://www.youtube.com/watch?v={entry['id']}\n"
    path.write_text(f"{header}\n{body}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-timestamps", action="store_true")
    ap.add_argument("--no-txt", action="store_true")
    ap.add_argument("--no-srt", action="store_true")
    ap.add_argument("--include-non-sermons", action="store_true")
    args = ap.parse_args()

    for d in (TEXT_DIR, SRT_DIR):
        d.mkdir(exist_ok=True)

    idx = load_index()
    rows, missing, suspect = [], [], []

    for vid, entry in sorted(idx.items(), key=lambda kv: kv[1].get("upload_date") or ""):
        if not entry.get("sermon") and not args.include_non_sermons:
            continue
        cues, source = cues_for(vid)
        if not cues:
            missing.append(entry.get("title") or vid)
            continue

        words = sum(len(c[-1].split()) for c in cues)
        if words < MIN_WORDS:
            suspect.append((entry.get("title") or vid, words))

        sp = DATA / "summaries" / f"{vid}.json"
        summary = json.loads(sp.read_text()) if sp.exists() else None

        md = transcript_path(entry)
        md.write_text(render_markdown(entry, cues, source,
                                      timestamps=not args.no_timestamps,
                                      summary=summary))
        stem = md.stem
        if not args.no_txt:
            write_txt(TEXT_DIR / f"{stem}.txt", entry, cues)
        if not args.no_srt:
            write_srt(SRT_DIR / f"{stem}.srt", cues)

        rows.append({
            "date": entry.get("upload_date") or "unknown",
            "title": entry["title"],
            "file": md.name,
            "words": words,
            "source": source,
            "series": entry.get("series_primary") or "(standalone)",
            "duration": entry.get("duration") or 0,
        })

    # Filenames encode the date, so a backfilled date leaves the old file
    # orphaned. Drop anything not produced by this run.
    expected = {r["file"].rsplit(".", 1)[0] for r in rows}
    stale = 0
    for d, ext in ((TRANSCRIPTS, ".md"), (TEXT_DIR, ".txt"), (SRT_DIR, ".srt")):
        for f in d.glob(f"*{ext}"):
            if f.stem not in expected:
                f.unlink()
                stale += 1
    if stale:
        print(f"removed {stale} stale output files")

    write_readme(rows)
    total = sum(r["words"] for r in rows)
    print(f"rendered {len(rows)} transcripts, {total:,} words "
          f"({total / max(len(rows), 1):,.0f} avg)")
    if missing:
        print(f"awaiting transcription: {len(missing)}")
    if suspect:
        print(f"suspiciously short ({MIN_WORDS} words or fewer) - check these:")
        for t, w in suspect:
            print(f"    {w:>5} words  {t[:60]}")


def write_readme(rows):
    by_series = {}
    for r in rows:
        by_series.setdefault(r["series"], []).append(r)

    hours = sum(r["duration"] for r in rows) / 3600
    words = sum(r["words"] for r in rows)
    lines = [
        "# Canvas Community Church - Sermon Transcripts",
        "",
        f"{len(rows)} sermons, {hours:.0f} hours of audio, {words:,} words.",
        "",
        "**Browse and search it: "
        "<https://average-gary.github.io/canvas-church-transcripts/>**",
        "",
        "Source: <https://www.youtube.com/@CanvasWinchester>",
        "",
        "Transcribed locally with whisper.cpp (`large-v3-turbo`). Each transcript "
        "links back to the exact moment on YouTube.",
        "",
        "- `transcripts/` - markdown with clickable timestamps",
        "- `text/` - plain prose, no timestamps",
        "- `srt/` - subtitle files",
        "",
        "Search across everything:",
        "",
        "```",
        "python3 scripts/search.py \"the kingdom of God\"",
        "```",
        "",
        "## Series",
        "",
    ]
    for series in sorted(by_series, key=lambda s: (s == "(standalone)", s.lower())):
        items = sorted(by_series[series], key=lambda r: r["date"], reverse=True)
        lines.append(f"### {series} ({len(items)})")
        lines.append("")
        lines.append("| Date | Sermon | Words |")
        lines.append("| --- | --- | --- |")
        for r in items:
            lines.append(f"| {r['date']} | [{r['title']}](transcripts/{r['file']}) | {r['words']:,} |")
        lines.append("")

    (ROOT / "README.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
