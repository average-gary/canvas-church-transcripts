"""Shared helpers for the Canvas Winchester transcript pipeline."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAPTIONS = ROOT / "captions"
AUDIO = ROOT / "audio"
TRANSCRIPTS = ROOT / "transcripts"
LOGS = ROOT / "logs"

CHANNEL = "https://www.youtube.com/@CanvasWinchester"
INDEX = DATA / "index.json"
MANUAL_SERIES = DATA / "series_manual.json"

for d in (DATA, CAPTIONS, AUDIO, TRANSCRIPTS, LOGS):
    d.mkdir(exist_ok=True)


def run(cmd, **kw):
    """Run a command, returning CompletedProcess. Never raises on non-zero."""
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def load_index():
    if not INDEX.exists():
        return {}
    return merge_manual_series(json.loads(INDEX.read_text()))


def merge_manual_series(idx):
    """Fold in series the church preached but never made a playlist for.

    Read from data/series_manual.json ({video_id: [series, ...]}), which is
    hand-maintained: scripts/analyze_series.py finds the candidates and quotes
    the announcement from the pulpit, a human decides. It cannot live in
    index.json because fetch_playlists.py rebuilds `series` from the playlists
    on every run and would wipe it.
    """
    if not MANUAL_SERIES.exists():
        return idx
    for vid, names in json.loads(MANUAL_SERIES.read_text()).items():
        entry = idx.get(vid)
        if not entry:
            continue
        series = entry.setdefault("series", [])
        series += [n for n in names if n not in series]
        entry["series_primary"] = series[0]
    return idx


def save_index(idx):
    INDEX.write_text(json.dumps(idx, indent=2, sort_keys=True))


def slugify(title, maxlen=60):
    s = re.sub(r"[^\w\s-]", "", title.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:maxlen].rstrip("-") or "untitled"


def transcript_path(entry):
    date = entry.get("upload_date") or "0000-00-00"
    return TRANSCRIPTS / f"{date}-{slugify(entry['title'])}-{entry['id']}.md"


# ---------------------------------------------------------------- caption parse

def parse_json3(path):
    """YouTube json3 captions -> list of (start_seconds, end_seconds, text).

    Auto-caption tracks interleave real cues with rolling-window continuation
    events flagged `aAppend`; those only carry whitespace and are dropped.
    """
    doc = json.loads(Path(path).read_text())
    cues = []
    for ev in doc.get("events", []):
        if "segs" not in ev or ev.get("aAppend"):
            continue
        text = "".join(s.get("utf8", "") for s in ev["segs"])
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000.0
        end = start + (ev.get("dDurationMs") or 0) / 1000.0
        cues.append((start, end, text))
    return cues


def collapse_loops(cues, limit=3):
    """Drop Whisper's looped repeats, keeping at most `limit` in a row.

    Over music or silence whisper.cpp latches onto a phrase and emits it for
    minutes at a time - one sermon here repeats a single line 1,995 times. Real
    rhetorical repetition lands inside one segment rather than across several,
    so a longer run of identical consecutive segments is always an artifact.
    """
    out, run = [], 0
    for cue in cues:
        run = run + 1 if out and cue[2] == out[-1][2] else 0
        if run < limit:
            out.append(cue)
    return out


def parse_whisper_json(path):
    """whisper.cpp --output-json -> list of (start_seconds, end_seconds, text)."""
    doc = json.loads(Path(path).read_text())
    cues = []
    for seg in doc.get("transcription", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        off = seg.get("offsets", {})
        cues.append((off.get("from", 0) / 1000.0, off.get("to", 0) / 1000.0,
                     fix_caps(text)))
    return collapse_loops(cues)


# ---------------------------------------------------------------- capitalisation

# Proper nouns Whisper sometimes leaves lowercase. Words that are usually
# *correct* in lowercase are deliberately absent - church, gospel, father, son,
# spirit, devil, disciples - as are book names that double as ordinary English
# ("a good job", "he acts", "the numbers", "forgot to mark"); those are handled
# by BOOK_WORDS below, which only fires next to a chapter number. `advent` and
# `lent` are absent for the same reason ("the advent of machinery", "no one would
# have lent him the money"); Whisper capitalises the church seasons on its own.
PROPER = (
    "God Jesus Christ Messiah Lord Bible Scripture Scriptures Gospels Torah "
    "Christian Christians Christianity Christlike Christlikeness Christology "
    "Antichrist Emmanuel Immanuel Christmas Easter Passover "
    "Pentecost Sabbath Satan Pharaoh Caesar "
    "Israel Israelite Israelites Judah Jerusalem Zion Egypt Egyptian Egyptians "
    "Babylon Babylonian Assyria Persia Rome Roman Romans Corinth Ephesus Philippi "
    "Galilee Nazareth Bethlehem Capernaum Judea Samaria Samaritan Samaritans "
    "Eden Sinai Damascus Nineveh Bethel Gethsemane Golgotha Calvary "
    "Gentile Gentiles Jew Jews Jewish Pharisee Pharisees Sadducee Sadducees "
    "Levite Levites Israelites Canaan Canaanite "
    "Paul Peter Moses Aaron Abraham Sarah Isaac Jacob Solomon David Jonathan "
    "Elijah Elisha Isaiah Jeremiah Ezekiel Hosea Joel Amos Obadiah Micah Nahum "
    "Habakkuk Zephaniah Haggai Zechariah Malachi Nehemiah Esther Ezra Jonah "
    "Daniel Joshua Samuel Ruth Deborah Gideon Hezekiah Josiah Nebuchadnezzar "
    "Goliath Timothy Titus Philemon Barnabas Silas Stephen Simeon Lazarus "
    "Martha Mary Pilate Herod Matthew Luke John James Joseph Nathan Philip Thomas"
).split()

PHRASES = {
    "holy spirit": "Holy Spirit", "holy ghost": "Holy Ghost",
    "old testament": "Old Testament", "new testament": "New Testament",
    "ten commandments": "Ten Commandments", "lord's prayer": "Lord's Prayer",
    "holy week": "Holy Week", "good friday": "Good Friday",
    "palm sunday": "Palm Sunday", "ash wednesday": "Ash Wednesday",
    "red sea": "Red Sea", "dead sea": "Dead Sea", "holy land": "Holy Land",
    "sermon on the mount": "Sermon on the Mount",
    "son of god": "Son of God", "son of man": "Son of Man",
    "kingdom of god": "kingdom of God", "word of god": "word of God",
    "fruit of the spirit": "fruit of the Spirit",
    "fruits of the spirit": "fruits of the Spirit",
    "gift of the spirit": "gift of the Spirit",
    "gifts of the spirit": "gifts of the Spirit",
}

# Capitalised only beside a chapter number or after "book/letter/gospel of",
# because each of these is also an ordinary English word.
BOOK_WORDS = (
    "genesis exodus leviticus numbers deuteronomy joshua judges ruth samuel kings "
    "chronicles ezra nehemiah esther job psalm psalms proverbs ecclesiastes song "
    "isaiah jeremiah lamentations ezekiel daniel hosea joel amos obadiah jonah "
    "micah nahum habakkuk zephaniah haggai zechariah malachi matthew mark luke "
    "john acts romans corinthians galatians ephesians philippians colossians "
    "thessalonians timothy titus philemon hebrews james peter jude revelation"
).split()

_CANON = {w.lower(): w for w in PROPER}
_PROPER_RE = re.compile(r"\b(%s)\b" % "|".join(sorted(_CANON, key=len, reverse=True)))
_PHRASE_RE = re.compile(r"\b(%s)\b" % "|".join(sorted(PHRASES, key=len, reverse=True)),
                        re.I)
_BOOKS_ALT = "|".join(sorted(BOOK_WORDS, key=len, reverse=True))
_BOOK_NUM_RE = re.compile(r"\b(%s)(\s+\d)" % _BOOKS_ALT)
_BOOK_OF_RE = re.compile(r"\b((?:book|letter|gospel|prophet)\s+of\s+)(%s)\b" % _BOOKS_ALT)
_BOOK_ORD_RE = re.compile(r"\b(first|second|third|1|2|3)(\s+)(%s)\b" % _BOOKS_ALT, re.I)
# "false gospels" is a counterfeit message, not the four books. "other gospels"
# is either one; it stays lowercase because a wrong capital reads worse than a
# missing one, and Whisper capitalises it itself where it means the four.
_MAKES_COMMON = re.compile(r"\b(?:false|counterfeit|other)\s+$", re.I)


def _expand_phrase(m):
    """Substitute a PHRASES value, keeping a capital the text already had.

    Values whose first word is ordinary English are stored lowercase ("kingdom
    of God"), so replacing blindly would demote a legitimate "Kingdom of God" at
    the start of a sentence.
    """
    out = PHRASES[m.group(1).lower()]
    return out[0].upper() + out[1:] if m.group(1)[0].isupper() else out


def _raise_proper(m):
    if m.group(1) == "gospels" and _MAKES_COMMON.search(m.string[:m.start()]):
        return m.group(1)
    return _CANON[m.group(1)]


def fix_caps(text):
    """Capitalise proper nouns Whisper left lowercase.

    Only ever raises case, and only for words that are proper nouns in every
    ordinary use. In 218 of the 238 sermons here this changes a couple of dozen
    words; the wins are concentrated in transcriptions that came out uncased,
    which `unpunctuated()` finds and which are better re-run than patched.
    """
    text = _PHRASE_RE.sub(_expand_phrase, text)
    text = _PROPER_RE.sub(_raise_proper, text)
    text = _BOOK_NUM_RE.sub(lambda m: m.group(1).capitalize() + m.group(2), text)
    text = _BOOK_OF_RE.sub(lambda m: m.group(1) + m.group(2).capitalize(), text)
    text = _BOOK_ORD_RE.sub(
        lambda m: m.group(1) + m.group(2) + m.group(3).capitalize(), text)
    return text


def load_summary(vid):
    """Read a sermon summary with its proper nouns capitalised.

    The summarizer is inconsistent about them - "God's presence" and "god's
    love" both came back as themes - and every field here is displayed as-is,
    as theme labels and in each sermon's rail, so it gets the same fix_caps
    pass the transcripts get. Theme labels stay lowercase otherwise, which is
    what they want to be: fix_caps only ever raises a proper noun.
    """
    path = DATA / "summaries" / f"{vid}.json"
    if not path.exists():
        return {}

    def fix(value):
        if isinstance(value, str):
            return fix_caps(value)
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value

    return {k: fix(v) for k, v in json.loads(path.read_text()).items()}


def unpunctuated(cues, per_1k=20):
    """True when a transcription came out with no sentence punctuation.

    Whisper.cpp occasionally decodes an entire file in a lowercase, unpunctuated
    style - 20 of these 238 sermons did. Priming it with `--prompt` plus
    `--carry-initial-prompt` fixes it, so this is the gate for re-running rather
    than something `fix_caps` can paper over: no amount of casing repair puts
    sentence boundaries back.
    """
    body = " ".join(t for _, _, t in cues)
    words = re.findall(r"[A-Za-z']+", body)
    if len(words) < 200:
        return False
    sentences = sum(body.count(c) for c in ".?!")
    lone_i = sum(1 for w in words if w == "i")
    cap_i = sum(1 for w in words if w == "I")
    return 1000 * sentences / len(words) < per_1k or lone_i > cap_i


# ------------------------------------------------------------------- rendering

FILLER = re.compile(r"^\[(music|applause|laughter|inaudible|blank_audio)\]$", re.I)


def paragraphs(cues, gap=2.0, max_words=140, min_words=25):
    """Group cues into readable paragraphs.

    Breaks on a real silence (measured from the previous cue's end, since
    auto-caption cues overlap heavily) or once a paragraph gets long enough.
    Runt paragraphs below min_words are folded into the previous one so stray
    interjections don't each become their own block.
    """
    paras, cur, words, prev_end = [], [], 0, None
    for start, end, text in cues:
        if FILLER.match(text):
            continue
        silence = prev_end is not None and (start - prev_end) > gap
        if cur and (silence or words >= max_words):
            paras.append((cur[0][0], " ".join(t for _, _, t in cur)))
            cur, words = [], 0
        cur.append((start, end, text))
        words += len(text.split())
        prev_end = max(end, prev_end or 0)
    if cur:
        paras.append((cur[0][0], " ".join(t for _, _, t in cur)))

    merged = []
    for start, text in paras:
        if merged and len(text.split()) < min_words:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]} {text}")
        else:
            merged.append((start, text))
    # Again on the joined prose: "the holy" / "spirit came" arrives as two cues,
    # so a phrase split across a cue boundary is only fixable once they are one
    # string.
    return [(start, fix_caps(text)) for start, text in merged]


def hhmmss(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def render_markdown(entry, cues, source, timestamps=True, summary=None):
    date = entry.get("upload_date") or "unknown"
    dur = entry.get("duration") or 0
    lines = [
        "---",
        f"title: {json.dumps(entry['title'])}",
        f"video_id: {entry['id']}",
        f"url: https://www.youtube.com/watch?v={entry['id']}",
        f"date: {date}",
        f"duration: {hhmmss(dur)}",
        f"series: {json.dumps(entry.get('series_primary'))}",
        f"transcript_source: {source}",
        f"word_count: {sum(len(c[-1].split()) for c in cues)}",
    ]
    if summary:
        for field in ("themes", "scriptures"):
            if summary.get(field):
                lines.append(f"{field}: {json.dumps(summary[field])}")
    lines += [
        "---",
        "",
        f"# {entry['title']}",
        "",
        f"*{date} — [watch on YouTube](https://www.youtube.com/watch?v={entry['id']}) — {hhmmss(dur)}*",
        "",
    ]

    if summary and summary.get("summary") not in (None, "NO_SERMON"):
        if summary.get("big_idea"):
            lines += [f"> **{summary['big_idea']}**", ""]
        lines += ["## Summary", "", summary["summary"], ""]
        if summary.get("key_points"):
            lines += ["**Key points**", ""]
            lines += [f"- {p}" for p in summary["key_points"]]
            lines.append("")
        if summary.get("scriptures"):
            lines += [f"**Scripture:** {', '.join(summary['scriptures'])}", ""]
        lines += ["## Transcript", ""]
    for start, text in paragraphs(cues):
        if timestamps:
            stamp = hhmmss(start)
            link = f"https://www.youtube.com/watch?v={entry['id']}&t={int(start)}s"
            lines.append(f"**[{stamp}]({link})** {text}")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _self_check():
    loop = [(0.0, 1.0, "hi")] + [(float(i), i + 1.0, "Thank you.") for i in range(1, 50)]
    kept = collapse_loops(loop)
    assert [c[2] for c in kept] == ["hi"] + ["Thank you."] * 3, kept
    # A run that resumes after an interruption is counted afresh.
    assert len(collapse_loops([(0, 1, "a")] * 5 + [(1, 2, "b")] + [(2, 3, "a")] * 5)) == 7

    # Paragraphs break on real silence, not on ordinary cue spacing.
    tight = [(float(i * 3), i * 3 + 2.9, "word " * 10) for i in range(6)]
    assert len(paragraphs(tight, min_words=0)) == 1
    gapped = tight + [(100.0, 103.0, "word " * 10)]
    assert len(paragraphs(gapped, min_words=0)) == 2

    # Runts fold into the paragraph before them.
    assert len(paragraphs(gapped, min_words=25)) == 1
    assert hhmmss(3661) == "01:01:01"
    assert slugify("REVELATION: God's Story!") == "revelation-gods-story"

    # Capitalisation only ever fires on real proper nouns.
    assert fix_caps("the kingdom of god belongs to jesus") == \
        "the kingdom of God belongs to Jesus"
    assert fix_caps("led by the holy spirit") == "led by the Holy Spirit"
    assert fix_caps("turn to john 3:16") == "turn to John 3:16"
    assert fix_caps("read the book of numbers") == "read the book of Numbers"
    assert fix_caps("in first corinthians") == "in first Corinthians"
    assert fix_caps("the fruit of the spirit is love") == \
        "the fruit of the Spirit is love"
    assert fix_caps("growing in christlikeness") == "growing in Christlikeness"
    assert fix_caps("read the four gospels") == "read the four Gospels"
    # A phrase the text already capitalised keeps its capital.
    assert fix_caps("Kingdom of god is near") == "Kingdom of God is near"
    # ...and leaves ordinary English alone.
    for phrase in ("i did a good job today", "please mark your calendar",
                   "the numbers are down", "he acts like that", "our church",
                   "the gospel of grace", "a revelation to me", "godly living",
                   "they preach false gospels", "he lent him the money",
                   "before the advent of machinery", "in the spirit of unity"):
        assert fix_caps(phrase) == phrase, phrase

    # A typo'd video id in the overlay would be silently ignored otherwise.
    idx, manual = load_index(), {}
    if MANUAL_SERIES.exists():
        manual = json.loads(MANUAL_SERIES.read_text())
    for vid, names in manual.items():
        assert vid in idx, f"series_manual.json: {vid} is not in the index"
        assert names[0] in idx[vid]["series"] and idx[vid]["series_primary"], vid

    good = [(0, 1, "This is a sentence. So is this one. And another. Here I am.")] * 30
    assert not unpunctuated(good)
    bad = [(0, 1, "this is a sentence so is this one and another here i am")] * 30
    assert unpunctuated(bad)
    print("common.py self-check ok")


if __name__ == "__main__":
    _self_check()
