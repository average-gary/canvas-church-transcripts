"""Generate a short summary, key points and scripture references per sermon.

Uses the local `claude` CLI in headless mode, so it relies on the auth already
present on this machine rather than a separate API key. One call per sermon,
cached in data/summaries/<video_id>.json; re-running only fills in gaps.

  python3 scripts/summarize.py --limit 3        # try a few first
  python3 scripts/summarize.py                  # everything remaining
  python3 scripts/summarize.py --model opus     # slower, better
  python3 scripts/summarize.py --force <id>     # redo one

render.py folds any summary it finds into the transcript.
"""

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import DATA, ROOT, load_index, slugify, transcript_path

SUMMARIES = DATA / "summaries"
SUMMARIES.mkdir(parents=True, exist_ok=True)
TEXT_DIR = ROOT / "text"

PROMPT = """\
The text piped to you is an automatic transcript of one sermon from a church \
service. It is unedited, so it may contain transcription errors, announcements, \
and worship segments alongside the preaching.

Summarise the sermon itself. Return ONLY a JSON object, no prose or code fences:

{
  "summary": "2-3 sentences on what this sermon is about",
  "key_points": ["3 to 5 short phrases capturing the main movements"],
  "scriptures": ["Bible references cited, e.g. 'John 3:16-18'"],
  "themes": ["3 to 6 lowercase topical tags, e.g. 'forgiveness'"],
  "big_idea": "the single central claim, in one sentence"
}

Rules:
- Base everything strictly on the transcript; do not invent references.
- Use your judgement on garbled scripture references, but omit any you cannot
  reasonably identify rather than guessing.
- Write in your own words. Do not quote more than a few words at a time.
- If the transcript has no discernible sermon, return {"summary": "NO_SERMON"}.
"""


def summary_path(vid):
    return SUMMARIES / f"{vid}.json"


def extract_json(text):
    """Pull the first JSON object out of a model response."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON in response: {text[:160]}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON in response")


def transcript_text(vid, entry):
    stem = transcript_path(entry).stem
    p = TEXT_DIR / f"{stem}.txt"
    if p.exists():
        return p.read_text()
    md = transcript_path(entry)
    if md.exists():
        return md.read_text()
    return None


def summarize(vid, entry, model, timeout):
    body = transcript_text(vid, entry)
    if not body:
        return vid, "no-transcript", None
    try:
        res = subprocess.run(
            ["claude", "-p", PROMPT, "--model", model],
            input=body, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return vid, "timeout", None
    if res.returncode != 0:
        return vid, f"cli-error: {(res.stderr or '').strip()[:120]}", None
    try:
        data = extract_json(res.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        return vid, f"parse-error: {exc}", None

    data["video_id"] = vid
    data["title"] = entry.get("title")
    data["model"] = model
    summary_path(vid).write_text(json.dumps(data, indent=2))
    return vid, "ok", data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--force", nargs="*", metavar="VIDEO_ID",
                    help="redo these ids (or all, if given with no ids)")
    args = ap.parse_args()

    idx = load_index()
    if args.force is not None:
        targets = args.force or [v for v, e in idx.items() if e.get("sermon")]
        for v in targets:
            summary_path(v).unlink(missing_ok=True)

    todo = [v for v, e in sorted(idx.items(), key=lambda kv: kv[1].get("upload_date") or "")
            if e.get("sermon") and not summary_path(v).exists()
            and transcript_text(v, e)]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to summarise")
        return

    print(f"summarising {len(todo)} sermons with {args.model}, {args.workers} at a time\n")
    counts, failures = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(summarize, v, idx[v], args.model, args.timeout): v
                   for v in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            vid, status, data = fut.result()
            key = status.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            if status != "ok":
                failures.append((vid, status))
            title = (idx[vid].get("title") or vid)[:44]
            note = data.get("big_idea", "")[:60] if data else status
            print(f"  [{n}/{len(todo)}] {title:<46} {note}", flush=True)

    print(f"\n{counts}")
    if failures:
        print(f"{len(failures)} failed; re-run to retry:")
        for vid, status in failures[:10]:
            print(f"  {vid}  {status}")


if __name__ == "__main__":
    main()
