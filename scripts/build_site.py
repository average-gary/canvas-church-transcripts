"""Build a static site from the transcript archive.

Emits plain HTML into docs/ (GitHub Pages serves that folder directly). All
internal links are relative, so the output works from file://, a local server,
or a project subpath like /canvas-church-transcripts/ without reconfiguration.

Search is layered:
  - Pagefind indexes only the sermon transcripts (they carry data-pagefind-body,
    which makes Pagefind skip every other page) and provides ranked site search.
  - Each sermon page has an in-page occurrence finder. Search results link in
    with ?q=<term>, so a hit list with timestamps appears on arrival.

  python3 scripts/build_site.py
  python3 scripts/build_site.py --limit 5     # quick iteration on templates
"""

import argparse
import html
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime

from common import (ROOT, hhmmss, load_index, load_summary, paragraphs, slugify,
                    transcript_path)
from render import cues_for

DOCS = ROOT / "docs"
SITE_SRC = ROOT / "site_src"

CHANNEL = "https://www.youtube.com/@CanvasWinchester"
SITE_TITLE = "Canvas Sermon Archive"

# Canonical books, longest names first so "Song of Solomon" wins over "Song".
BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges",
    "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles",
    "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Psalm",
    "Proverbs", "Ecclesiastes", "Song of Solomon", "Song of Songs", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
    "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai",
    "Zechariah", "Malachi", "Matthew", "Mark", "Luke", "John", "Acts", "Romans",
    "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
    "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy",
    "Titus", "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John",
    "2 John", "3 John", "Jude", "Revelation",
]
BOOK_ORDER = {b: i for i, b in enumerate(BOOKS)}
ALIAS = {"Psalm": "Psalms", "Song of Songs": "Song of Solomon"}
BOOK_RE = re.compile(
    r"^\s*((?:[123]\s*)?[A-Za-z][A-Za-z ]*?)\s*(\d.*)?$"
)

E = html.escape


def book_of(ref):
    """'1 Cor 13:4-7' -> '1 Corinthians', or None if unrecognised."""
    m = BOOK_RE.match(ref or "")
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    low = name.lower().rstrip(".")
    for book in sorted(BOOKS, key=len, reverse=True):
        b = book.lower()
        if low == b or low == b.replace(" ", "") or b.startswith(low) and len(low) >= 3:
            return ALIAS.get(book, book)
    return None


# ------------------------------------------------------------------ page shell

def shell(depth, title, body, desc="", extra_head="", extra_body=""):
    root = "../" * depth or "./"
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)} · {E(SITE_TITLE)}</title>
<meta name="description" content="{E(desc or SITE_TITLE)}">
<link rel="stylesheet" href="{root}assets/styles.css">
{extra_head}
</head>
<body data-root="{root}">
<a class="skip" href="#main">Skip to content</a>
<header class="masthead" data-pagefind-ignore>
  <div class="masthead-in">
    <a class="wordmark" href="{root}">Canvas<span>/</span>Archive</a>
    <nav>
      <a href="{root}search/">Search</a>
      <a href="{root}all/">All sermons</a>
      <a href="{root}series/">Series</a>
      <a href="{root}themes/">Themes</a>
      <a href="{root}scripture/">Scripture</a>
      <a href="{root}timeline/">Timeline</a>
    </nav>
    <button class="icon-btn" id="theme-toggle" type="button">dark</button>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site" data-pagefind-ignore>
  <div class="wrap">
    <p>Transcripts of sermons published by Canvas Community Church at
      <a href="{CHANNEL}">@CanvasWinchester</a>. Sermon content belongs to the church.</p>
    <p>Transcribed locally with whisper.cpp (large-v3-turbo). This is unedited
      machine transcription: expect errors in proper nouns and scripture
      references, and check the recording before quoting.</p>
    <p class="hint">Press <kbd>/</kbd> to search · <kbd>f</kbd> find in page · <kbd>n</kbd> next hit</p>
  </div>
</footer>
<script src="{root}assets/app.js" defer></script>
{extra_body}
</body>
</html>
"""


def stat(v, k):
    return f'<div class="stat"><span class="v">{v}</span><span class="k label">{E(k)}</span></div>'


def sermon_rows(sermons, show_series=True):
    head = "<tr><th>Date</th><th>Sermon</th>"
    head += "<th>Series</th>" if show_series else ""
    head += "<th style='text-align:right'>Words</th><th style='text-align:right'>Length</th></tr>"
    rows = []
    for s in sermons:
        series_cell = ""
        if show_series:
            if s["series"]:
                series_cell = (f'<td class="t-series"><a href="{s["up"]}series/{s["series_slug"]}/">'
                               f'{E(s["series"])}</a></td>')
            else:
                series_cell = '<td class="t-series">—</td>'
        rows.append(
            f'<tr><td class="t-date">{s["date"]}</td>'
            f'<td class="t-title"><a href="{s["up"]}s/{s["slug"]}/">{E(s["title"])}</a></td>'
            f'{series_cell}'
            f'<td class="t-num">{s["words"]:,}</td>'
            f'<td class="t-num">{hhmmss(s["duration"])}</td></tr>'
        )
    return f'<table class="tbl"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


# ------------------------------------------------------------------ collection

def collect(limit=None):
    idx = load_index()
    out = []
    for vid, e in idx.items():
        if not e.get("sermon"):
            continue
        cues, source = cues_for(vid)
        if not cues:
            continue
        summary = load_summary(vid)
        date = e.get("upload_date") or "0000-00-00"
        title = e.get("title") or vid
        paras = paragraphs(cues)
        # Same stem render.py used, so the text/ and srt/ downloads line up.
        stem = transcript_path(e).stem
        out.append({
            "id": vid,
            "title": title,
            "date": date,
            "duration": e.get("duration") or 0,
            "series": e.get("series_primary"),
            "series_slug": slugify(e["series_primary"]) if e.get("series_primary") else "",
            "all_series": e.get("series") or [],
            "slug": stem,
            "words": sum(len(c[-1].split()) for c in cues),
            "source": source,
            "paras": paras,
            "cues": cues,
            "summary": summary,
            "themes": summary.get("themes") or [],
            "scriptures": summary.get("scriptures") or [],
            "up": "../../",
        })
    out.sort(key=lambda s: s["date"], reverse=True)
    return out[:limit] if limit else out


# ----------------------------------------------------------------- sermon page

def sermon_page(s, prev_s, next_s):
    sm = s["summary"]
    # The player stays pinned; everything below it scrolls in its own region, so
    # a rail taller than the viewport is reachable without scrolling the page.
    player = f'<div class="player-frame"><div id="player" data-video="{s["id"]}"></div></div>'
    rail = []

    rail.append('<table class="meta-tbl">')
    rail.append(f'<tr><th>Date</th><td>{s["date"]}</td></tr>')
    rail.append(f'<tr><th>Length</th><td>{hhmmss(s["duration"])}</td></tr>')
    rail.append(f'<tr><th>Words</th><td>{s["words"]:,}</td></tr>')
    if s["series"]:
        rail.append(f'<tr><th>Series</th><td><a href="../../series/{s["series_slug"]}/">'
                    f'{E(s["series"])}</a></td></tr>')
    rail.append(f'<tr><th>Video</th><td><a href="https://www.youtube.com/watch?v={s["id"]}">'
                f'YouTube ↗</a></td></tr>')
    rail.append('</table>')

    if sm.get("big_idea"):
        rail.append('<div class="rail-block"><h2>Big idea</h2>'
                    f'<p class="big-idea">{E(sm["big_idea"])}</p></div>')
    if sm.get("summary") and sm["summary"] != "NO_SERMON":
        rail.append(f'<div class="rail-block"><h2>Summary</h2><p>{E(sm["summary"])}</p></div>')
    if sm.get("key_points"):
        pts = "".join(f"<li>{E(p)}</li>" for p in sm["key_points"])
        rail.append(f'<div class="rail-block"><h2>Key points</h2><ul>{pts}</ul></div>')
    if s["scriptures"]:
        refs = []
        for r in s["scriptures"]:
            book = book_of(r)
            refs.append(f'<a class="ref" href="../../scripture/{slugify(book)}/">{E(r)}</a>'
                        if book else f'<span class="ref">{E(r)}</span>')
        rail.append('<div class="rail-block"><h2>Scripture</h2>'
                    f'<div class="rail-list">{"".join(refs)}</div></div>')
    if s["themes"]:
        chips = "".join(
            f'<a class="chip" href="../../themes/{slugify(t)}/">{E(t)}</a>' for t in s["themes"]
        )
        rail.append(f'<div class="rail-block"><h2>Themes</h2><div class="rail-list">{chips}</div></div>')

    rail.append('<div class="rail-block"><h2>Export</h2><div class="rail-list">'
                f'<a class="chip" href="../../text/{s["slug"]}.txt">plain text</a>'
                f'<a class="chip" href="../../srt/{s["slug"]}.srt">subtitles</a>'
                '</div></div>')

    nav = []
    if prev_s:
        nav.append(f'<a href="../{prev_s["slug"]}/">← {E(prev_s["title"][:34])}</a>')
    if next_s:
        nav.append(f'<a href="../{next_s["slug"]}/">{E(next_s["title"][:34])} →</a>')
    if nav:
        rail.append('<div class="rail-block"><h2>Nearby</h2>'
                    f'<div class="hint" style="display:grid;gap:.35rem">{"".join(nav)}</div></div>')

    cues_html = []
    for start, text in s["paras"]:
        anchor = f"t{int(start)}"
        cues_html.append(
            f'<div class="cue" id="{anchor}" data-t="{start:.1f}">'
            f'<a class="seek" href="https://www.youtube.com/watch?v={s["id"]}&amp;t={int(start)}s">'
            f'{hhmmss(start)}</a>'
            f'<p class="said">{E(text)}</p></div>'
        )

    # Pagefind parses inline meta/filter attributes as comma-separated pairs, so
    # a comma inside a value would split it into a bogus key.
    def pf(v):
        return E(str(v).replace(",", ";"))

    series_meta = pf(s["series"] or "Standalone")
    body = f"""
<div class="page-head">
  <div class="kicker label">{E(s["series"] or "Standalone sermon")} · {s["date"]}</div>
  <h1>{E(s["title"])}</h1>
</div>
<div class="cols">
  <aside class="rail" data-pagefind-ignore>{player}
    <div class="rail-scroll">{"".join(rail)}</div>
  </aside>
  <div>
    <div class="transcript-head">
      <div class="label">Transcript · {s["words"]:,} words</div>
      <div class="finder">
        <label class="label" for="find">Find</label>
        <input id="find" type="search" placeholder="word or phrase" autocomplete="off">
        <button class="icon-btn" id="find-prev" type="button" aria-label="Previous hit">↑</button>
        <button class="icon-btn" id="find-next" type="button" aria-label="Next hit">↓</button>
        <span class="count mono" id="find-count"></span>
        <button class="icon-btn" id="follow-toggle" type="button" aria-pressed="true"
                title="Scroll the transcript with playback">follow</button>
      </div>
    </div>
    <div id="cite-meta" data-title="{E(s["title"])}" data-date="{s["date"]}" data-video="{s["id"]}"></div>
    <div class="transcript" data-pagefind-body
         data-pagefind-meta="title:{pf(s["title"])},date:{s["date"]},series:{series_meta}"
         data-pagefind-filter="series:{series_meta}">
      <div data-pagefind-filter="year:{s["date"][:4]}">
        {"".join(cues_html)}
      </div>
    </div>
  </div>
</div>
"""
    desc = sm.get("summary") if sm.get("summary") not in (None, "NO_SERMON") else \
        f'Transcript of "{s["title"]}" preached at Canvas Community Church on {s["date"]}.'
    return shell(2, s["title"], body, desc)


# ------------------------------------------------------------------ index pages

def home(sermons):
    hours = sum(s["duration"] for s in sermons) / 3600
    words = sum(s["words"] for s in sermons)
    series = {s["series"] for s in sermons if s["series"]}
    recent = sermons[:12]
    by_series = Counter(s["series"] or "(standalone)" for s in sermons)

    cards = "".join(
        f'<a class="card" href="series/{slugify(name)}/"><span class="t">{E(name)}</span>'
        f'<span class="m label">{n} sermon{"s" if n != 1 else ""}</span></a>'
        for name, n in sorted(by_series.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        if name != "(standalone)"
    )

    body = f"""
<div class="page-head">
  <div class="kicker label">Canvas Community Church · Winchester</div>
  <h1>Five years of preaching, searchable to the second.</h1>
  <p class="dek">Every sermon published on the church's YouTube channel, transcribed
  and indexed. Search a word, land on the moment it was said, and click through to
  the recording.</p>
</div>
<div class="stats">
  {stat(len(sermons), "sermons")}
  {stat(f"{hours:.0f}", "hours")}
  {stat(f"{words / 1_000_000:.2f}M", "words")}
  {stat(len(series), "series")}
  {stat(sermons[-1]["date"][:4] + "–" + sermons[0]["date"][:4], "span")}
</div>

<div class="pair mt mb">
  <h2 class="grow" style="font-size:1.25rem">Most recent</h2>
  <a class="hint" href="all/">all {len(sermons)} sermons →</a>
</div>
{sermon_rows(recent_with_up(recent, 0))}

<div class="pair mt mb">
  <h2 class="grow" style="font-size:1.25rem">Series</h2>
  <a class="hint" href="series/">index →</a>
</div>
<div class="grid-cards">{cards}</div>
"""
    return shell(0, "Home", body,
                 "Searchable transcripts of every sermon from Canvas Community Church.")


def recent_with_up(sermons, depth):
    up = "../" * depth or "./"
    return [dict(s, up=up) for s in sermons]


def all_page(sermons):
    body = f"""
<div class="page-head">
  <div class="kicker label">Complete index</div>
  <h1>All {len(sermons)} sermons</h1>
  <p class="dek">Newest first. Sortable by eye — dates, word counts and lengths are
  tabular for scanning.</p>
</div>
{sermon_rows(recent_with_up(sermons, 1))}
"""
    return shell(1, "All sermons", body)


def series_index(sermons):
    groups = defaultdict(list)
    for s in sermons:
        for name in (s["all_series"] or []):
            groups[name].append(s)
        if not s["all_series"]:
            groups["(standalone)"].append(s)

    n_series = len([k for k in groups if k != "(standalone)"])
    rows = []
    for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        items.sort(key=lambda s: s["date"])
        span = f'{items[0]["date"][:7]} → {items[-1]["date"][:7]}'
        hrs = sum(i["duration"] for i in items) / 3600
        link = (f'<a href="{slugify(name)}/">{E(name)}</a>' if name != "(standalone)"
                else f'<a href="../all/">{E(name)}</a>')
        rows.append(f'<tr><td class="t-title">{link}</td>'
                    f'<td class="t-date">{span}</td>'
                    f'<td class="t-num">{len(items)}</td>'
                    f'<td class="t-num">{hrs:.1f}h</td></tr>')

    body = f"""
<div class="page-head">
  <div class="kicker label">Browse</div>
  <h1>Series</h1>
  <p class="dek">{n_series} teaching series, plus standalone sermons that were
  never gathered into one.</p>
</div>
<table class="tbl">
  <thead><tr><th>Series</th><th>Span</th><th style="text-align:right">Sermons</th>
  <th style="text-align:right">Hours</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
"""
    return shell(1, "Series", body)


def series_page(name, items):
    items = sorted(items, key=lambda s: s["date"])
    hrs = sum(i["duration"] for i in items) / 3600
    themes = Counter(t for i in items for t in i["themes"])
    chips = "".join(f'<a class="chip" href="../../themes/{slugify(t)}/">{E(t)} '
                    f'<span class="n">{n}</span></a>' for t, n in themes.most_common(18))
    body = f"""
<div class="page-head">
  <div class="kicker label">Series · {items[0]["date"][:7]} → {items[-1]["date"][:7]}</div>
  <h1>{E(name)}</h1>
  <p class="dek">{len(items)} sermon{"s" if len(items) != 1 else ""}, {hrs:.1f} hours.</p>
</div>
{sermon_rows(recent_with_up(items, 2), show_series=False)}
{f'<div class="mt"><div class="label mb">Recurring themes</div><div class="cloud">{chips}</div></div>' if chips else ''}
"""
    return shell(2, name, body, f"Sermons in the series {name} at Canvas Community Church.")


def themes_index(sermons):
    counts = Counter(t for s in sermons for t in s["themes"])
    chips = "".join(
        f'<a class="chip" href="{slugify(t)}/">{E(t)} <span class="n">{n}</span></a>'
        for t, n in counts.most_common()
    )
    body = f"""
<div class="page-head">
  <div class="kicker label">Browse</div>
  <h1>Themes</h1>
  <p class="dek">{len(counts)} topics drawn from the sermons themselves, ordered by how
  often they come up.</p>
</div>
<div class="cloud">{chips}</div>
"""
    return shell(1, "Themes", body)


def theme_page(theme, items):
    items = sorted(items, key=lambda s: s["date"], reverse=True)
    body = f"""
<div class="page-head">
  <div class="kicker label">Theme</div>
  <h1>{E(theme)}</h1>
  <p class="dek">{len(items)} sermon{"s" if len(items) != 1 else ""} on this theme.</p>
</div>
{sermon_rows(recent_with_up(items, 2))}
"""
    return shell(2, theme, body, f"Sermons about {theme} at Canvas Community Church.")


def scripture_index(sermons):
    books = defaultdict(list)
    unknown = 0
    for s in sermons:
        seen = set()
        for r in s["scriptures"]:
            b = book_of(r)
            if not b:
                unknown += 1
                continue
            if b not in seen:
                books[b].append(s)
                seen.add(b)

    rows = "".join(
        f'<tr><td class="t-title"><a href="{slugify(b)}/">{E(b)}</a></td>'
        f'<td class="t-num">{len(items)}</td></tr>'
        for b, items in sorted(books.items(), key=lambda kv: BOOK_ORDER.get(kv[0], 999))
    )
    body = f"""
<div class="page-head">
  <div class="kicker label">Browse</div>
  <h1>Scripture</h1>
  <p class="dek">Books cited across the archive, in canonical order. References come
  from automated summaries, so treat them as a pointer rather than a citation.</p>
</div>
<table class="tbl">
  <thead><tr><th>Book</th><th style="text-align:right">Sermons</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""
    return shell(1, "Scripture", body), books


def scripture_page(book, items):
    items = sorted(items, key=lambda s: s["date"], reverse=True)
    refs = Counter(r for s in items for r in s["scriptures"] if book_of(r) == book)
    chips = "".join(f'<span class="ref">{E(r)}</span> ' for r, _ in refs.most_common(40))
    body = f"""
<div class="page-head">
  <div class="kicker label">Scripture</div>
  <h1>{E(book)}</h1>
  <p class="dek">{len(items)} sermon{"s" if len(items) != 1 else ""} citing this book.</p>
</div>
<div class="mb"><div class="label mb">Passages cited</div><div class="cloud">{chips}</div></div>
{sermon_rows(recent_with_up(items, 2))}
"""
    return shell(2, book, body, f"Sermons citing {book} at Canvas Community Church.")


def timeline_page(sermons):
    by_month = Counter(s["date"][:7] for s in sermons)
    years = sorted({s["date"][:4] for s in sermons})
    peak = max(by_month.values()) if by_month else 1

    rows = []
    for y in years:
        cells = []
        for m in range(1, 13):
            key = f"{y}-{m:02d}"
            n = by_month.get(key, 0)
            if n:
                # Shade by density; alpha keeps it legible in both themes.
                a = 0.22 + 0.78 * (n / peak)
                cells.append(
                    f'<a class="tl-cell" data-n="{n}" href="#{key}" title="{key}: {n} sermons" '
                    f'style="background:color-mix(in srgb, var(--rubric) {a * 100:.0f}%, transparent)"></a>'
                )
            else:
                cells.append('<span class="tl-cell" aria-hidden="true"></span>')
        rows.append(f'<div class="tl-row"><span class="yr">{y}</span>'
                    f'<div class="tl-cells">{"".join(cells)}</div></div>')

    months = "".join(f"<span>{m}</span>" for m in
                     ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])

    listing = []
    for key in sorted(by_month, reverse=True):
        items = [s for s in sermons if s["date"][:7] == key]
        pretty = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
        listing.append(f'<h2 id="{key}" class="label mt mb">{pretty} · {len(items)}</h2>')
        listing.append(sermon_rows(recent_with_up(sorted(items, key=lambda s: s["date"]), 1)))

    body = f"""
<div class="page-head">
  <div class="kicker label">Overview</div>
  <h1>Timeline</h1>
  <p class="dek">Preaching frequency by month. Darker cells are busier months; gaps
  are weeks the channel did not publish.</p>
</div>
<div class="tl">
  <div class="tl-row"><span class="yr"></span><div class="tl-months">{months}</div></div>
  {"".join(rows)}
  <div class="tl-legend">
    <span class="label">sparse</span>
    <i style="background:color-mix(in srgb, var(--rubric) 22%, transparent)"></i>
    <i style="background:color-mix(in srgb, var(--rubric) 50%, transparent)"></i>
    <i style="background:color-mix(in srgb, var(--rubric) 78%, transparent)"></i>
    <i style="background:var(--rubric)"></i>
    <span class="label">busy</span>
  </div>
</div>
{"".join(listing)}
"""
    return shell(1, "Timeline", body)


def search_page(words):
    head = '<link href="../pagefind/pagefind-ui.css" rel="stylesheet">'
    body = f"""
<div class="page-head">
  <div class="kicker label">Full-text search</div>
  <h1>Search every sermon</h1>
  <p class="dek">Searches all {words / 1_000_000:.2f} million words. Open a result and
  the term is highlighted in the transcript with a timestamp for every occurrence, so
  you can jump straight to where it was said.</p>
</div>
<div id="search"></div>
<p class="hint mt">Try a phrase in quotes, or filter by series and year once results appear.</p>
"""
    script = """
<script src="../pagefind/pagefind-ui.js"></script>
<script>
  window.addEventListener('DOMContentLoaded', function () {
    new PagefindUI({
      element: '#search',
      showSubResults: false,
      showImages: false,
      pageSize: 12,
      excerptLength: 30,
      // Carry the query onto the sermon page, which then highlights every
      // occurrence and lists a timestamp for each.
      processResult: function (result) {
        var box = document.querySelector('.pagefind-ui__search-input');
        var q = box && box.value ? box.value.trim() : '';
        if (q && result.url.indexOf('q=') === -1) {
          result.url += (result.url.indexOf('?') === -1 ? '?' : '&')
            + 'q=' + encodeURIComponent(q);
        }
        return result;
      }
    });
    var q = new URLSearchParams(location.search).get('q');
    if (q) {
      var box = document.querySelector('.pagefind-ui__search-input');
      if (box) { box.value = q; box.dispatchEvent(new Event('input', {bubbles: true})); }
    }
  });
</script>
"""
    return shell(1, "Search", body, extra_head=head, extra_body=script)


# ------------------------------------------------------------------------ main

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="build only N sermons, for fast iteration")
    args = ap.parse_args()

    sermons = collect(args.limit)
    if not sermons:
        raise SystemExit("no rendered sermons found - run the pipeline first")

    if DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir(parents=True)

    (DOCS / "assets").mkdir()
    shutil.copy(SITE_SRC / "styles.css", DOCS / "assets" / "styles.css")
    shutil.copy(SITE_SRC / "app.js", DOCS / "assets" / "app.js")
    for sub in ("text", "srt"):
        if (ROOT / sub).exists():
            shutil.copytree(ROOT / sub, DOCS / sub)
    (DOCS / ".nojekyll").write_text("")

    # Sermon pages, with prev/next by date.
    chron = sorted(sermons, key=lambda s: s["date"])
    for i, s in enumerate(chron):
        prev_s = chron[i - 1] if i else None
        next_s = chron[i + 1] if i + 1 < len(chron) else None
        write(DOCS / "s" / s["slug"] / "index.html", sermon_page(s, prev_s, next_s))

    write(DOCS / "index.html", home(sermons))
    write(DOCS / "all" / "index.html", all_page(sermons))
    write(DOCS / "search" / "index.html", search_page(sum(s["words"] for s in sermons)))
    write(DOCS / "timeline" / "index.html", timeline_page(sermons))

    write(DOCS / "series" / "index.html", series_index(sermons))
    groups = defaultdict(list)
    for s in sermons:
        for name in (s["all_series"] or []):
            groups[name].append(s)
    for name, items in groups.items():
        write(DOCS / "series" / slugify(name) / "index.html", series_page(name, items))

    write(DOCS / "themes" / "index.html", themes_index(sermons))
    themes = defaultdict(list)
    for s in sermons:
        for t in s["themes"]:
            themes[t].append(s)
    for t, items in themes.items():
        write(DOCS / "themes" / slugify(t) / "index.html", theme_page(t, items))

    scripture_html, books = scripture_index(sermons)
    write(DOCS / "scripture" / "index.html", scripture_html)
    for b, items in books.items():
        write(DOCS / "scripture" / slugify(b) / "index.html", scripture_page(b, items))

    pages = sum(1 for _ in DOCS.rglob("index.html"))
    size = sum(f.stat().st_size for f in DOCS.rglob("*") if f.is_file()) / 1e6
    print(f"built {pages} pages ({size:.1f} MB) -> {DOCS}")
    print(f"  {len(sermons)} sermons, {len(groups)} series, {len(themes)} themes, {len(books)} books")
    print("\nnext: npx pagefind --site docs   (builds the search index)")


if __name__ == "__main__":
    main()
