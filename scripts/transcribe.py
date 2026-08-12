"""Transcribe sermons locally with whisper.cpp.

Defaults to every video flagged `sermon` in the index that does not yet have a
transcription. Designed for a long unattended run: each video is independent,
artifacts land in data/whisper/, and re-running picks up where it left off.
Audio is deleted after a successful transcription to bound disk use.

  python3 scripts/transcribe.py                     # all remaining sermons
  python3 scripts/transcribe.py --limit 3           # smoke test
  python3 scripts/transcribe.py --shortest-first    # bank quick wins early
  YT_COOKIES_FROM_BROWSER=chrome python3 scripts/transcribe.py

Env:
  WHISPER_MODEL              path to a ggml model (default ~/models/ggml-large-v3-turbo.bin)
  YT_COOKIES_FROM_BROWSER    browser to pull YouTube cookies from
"""

import argparse
import os
import sys
import time
from pathlib import Path

from common import (AUDIO, DATA, LOGS, load_index, parse_whisper_json, run,
                    unpunctuated)

MODEL = Path(os.environ.get(
    "WHISPER_MODEL", Path.home() / "models" / "ggml-large-v3-turbo.bin"
))
WHISPER_JSON = DATA / "whisper"
WHISPER_JSON.mkdir(parents=True, exist_ok=True)
FAILLOG = LOGS / "failures.log"

# Whisper takes its cue on punctuation and casing from the text it thinks came
# before. Left to itself it decoded 20 of these sermons entirely uncased and
# unpunctuated; primed with a sentence in the register it is about to hear, and
# with --carry-initial-prompt so the priming is repeated for every window rather
# than fading after the first thirty seconds, it does not.
PRIME = ("Good morning, church. Let's open our Bibles together. Today we are "
         "continuing our series, and I want us to see what God is doing in this "
         "passage. Jesus said, \"Follow me.\" Let's pray.")


def done(vid):
    p = WHISPER_JSON / f"{vid}.json"
    return p.exists() and p.stat().st_size > 0


def download_audio(vid, tries=3):
    """Fetch audio and return a 16 kHz mono WAV path, retrying on failure."""
    wav = AUDIO / f"{vid}.wav"
    if wav.exists() and wav.stat().st_size > 0:
        return wav

    raw = next(iter(AUDIO.glob(f"{vid}.m4a")), None)
    last = ""
    for attempt in range(1, tries + 1):
        if raw and raw.exists():
            break
        cmd = ["yt-dlp", "--no-warnings", "--retries", "5",
               "-f", "bestaudio[ext=m4a]/bestaudio",
               "--extract-audio", "--audio-format", "m4a",
               "-o", str(AUDIO / "%(id)s.%(ext)s"),
               f"https://www.youtube.com/watch?v={vid}"]
        browser = os.environ.get("YT_COOKIES_FROM_BROWSER")
        if browser:
            cmd[1:1] = ["--cookies-from-browser", browser]
        res = run(cmd)
        raw = AUDIO / f"{vid}.m4a"
        if raw.exists():
            break
        last = (res.stderr or "").strip()[-200:]
        if attempt < tries:
            time.sleep(10 * attempt)
    if not raw or not raw.exists():
        raise RuntimeError(f"audio download failed: {last}")

    res = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
    if not wav.exists() or wav.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg failed: {(res.stderr or '').strip()[-200:]}")
    raw.unlink(missing_ok=True)
    return wav


def transcribe(vid, wav, threads, nice_level=0, prompt=PRIME, out=None):
    out = out or WHISPER_JSON / vid
    cmd = ["whisper-cli", "-m", str(MODEL), "-f", str(wav),
           "-l", "en", "-t", str(threads),
           "--output-json", "--no-prints", "-of", str(out)]
    if prompt:
        cmd += ["--prompt", prompt, "--carry-initial-prompt"]
    if nice_level:
        cmd = ["nice", "-n", str(nice_level)] + cmd
    res = run(cmd)
    if not Path(f"{out}.json").exists():
        raise RuntimeError(f"whisper failed: {(res.stderr or '').strip()[-200:]}")


def redo(vid, wav, threads, nice_level=0):
    """Re-transcribe over an existing result, keeping the better of the two.

    A re-run is not guaranteed to be an improvement and the old transcription is
    the only copy, so the new one has to earn the slot.
    """
    tmp = WHISPER_JSON / f"{vid}.retry"
    new = Path(f"{tmp}.json")
    try:
        transcribe(vid, wav, threads, nice_level, out=tmp)
        before = unpunctuated(parse_whisper_json(WHISPER_JSON / f"{vid}.json"))
        after = unpunctuated(parse_whisper_json(new))
        if before and not after:
            new.replace(WHISPER_JSON / f"{vid}.json")
            return True, "repunctuated"
        return False, "re-run was no better, kept the original"
    finally:
        new.unlink(missing_ok=True)


def parse_hm(value):
    """'18:00' -> (18, 0). Used for the evening ramp-up."""
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
        return h, m
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {value!r}")


def plan_now(args):
    """Pick (threads, nice) for the next video based on the clock.

    Keeps the machine usable during the day and only takes the extra cores once
    the ramp time passes.
    """
    if not args.ramp_at:
        return args.threads, args.nice
    now = time.localtime()
    if (now.tm_hour, now.tm_min) >= args.ramp_at:
        return args.ramp_threads, args.ramp_nice
    return args.threads, args.nice


def fmt(sec):
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--threads", type=int, default=8,
                    help="whisper threads (before the ramp, if one is set)")
    ap.add_argument("--nice", type=int, default=0,
                    help="scheduling priority, 0-20; higher yields more to you")
    ap.add_argument("--ramp-at", type=parse_hm, metavar="HH:MM",
                    help="local time to switch to --ramp-threads, e.g. 18:00")
    ap.add_argument("--ramp-threads", type=int, default=10)
    ap.add_argument("--ramp-nice", type=int, default=0)
    ap.add_argument("--all-videos", action="store_true",
                    help="include non-sermon videos too")
    ap.add_argument("--shortest-first", action="store_true")
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--sleep", type=float, default=2.0,
                    help="pause between videos, to stay polite to YouTube")
    ap.add_argument("--redo-unpunctuated", action="store_true",
                    help="re-run sermons whose transcription came out uncased and "
                         "unpunctuated, keeping the old one unless the new one is better")
    ap.add_argument("--no-prompt", action="store_true",
                    help="disable the punctuation priming prompt")
    args = ap.parse_args()

    if not MODEL.exists():
        sys.exit(f"model not found: {MODEL}")

    idx = load_index()
    if args.redo_unpunctuated:
        todo = [v for v, e in idx.items()
                if e.get("sermon") and done(v)
                and unpunctuated(parse_whisper_json(WHISPER_JSON / f"{v}.json"))]
    else:
        todo = [v for v, e in idx.items()
                if (args.all_videos or e.get("sermon")) and not done(v)]
    if args.shortest_first:
        todo.sort(key=lambda v: idx[v].get("duration") or 0)
    else:
        todo.sort(key=lambda v: idx[v].get("upload_date") or "")
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("nothing to transcribe - all done")
        return

    total_sec = sum(idx[v].get("duration") or 0 for v in todo)
    threads, nice_level = plan_now(args)
    print(f"{len(todo)} videos, {fmt(total_sec)} of audio")
    print(f"model: {MODEL.name}")
    print(f"starting at {threads} threads, nice {nice_level}")
    if args.ramp_at:
        print(f"ramping to {args.ramp_threads} threads (nice {args.ramp_nice}) "
              f"at {args.ramp_at[0]:02d}:{args.ramp_at[1]:02d} local")
    print()

    start_all, ok, failed, sec_done = time.time(), 0, [], 0
    kept_worse = []
    for n, vid in enumerate(todo, 1):
        e = idx[vid]
        dur = e.get("duration") or 0
        # Re-check the clock each video so a long run picks up the ramp.
        new_plan = plan_now(args)
        if new_plan != (threads, nice_level):
            threads, nice_level = new_plan
            print(f"--- ramping up: {threads} threads, nice {nice_level} "
                  f"({time.strftime('%H:%M')})", flush=True)
        print(f"[{n}/{len(todo)}] {e.get('title', vid)[:54]} ({fmt(dur)})", flush=True)
        t0 = time.time()
        try:
            wav = download_audio(vid)
            prompt = None if args.no_prompt else PRIME
            if args.redo_unpunctuated:
                better, why = redo(vid, wav, threads, nice_level)
                print(f"        {why}", flush=True)
                if not better:
                    kept_worse.append(vid)
            else:
                transcribe(vid, wav, threads, nice_level, prompt=prompt)
            if not args.keep_audio:
                wav.unlink(missing_ok=True)
            el = time.time() - t0
            ok += 1
            sec_done += dur
            rate = sec_done / (time.time() - start_all)
            remaining = (total_sec - sec_done) / rate if rate else 0
            print(f"        {el / 60:.1f}m ({dur / el:.0f}x)  "
                  f"done {ok}/{len(todo)}  eta {fmt(remaining)}", flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad video must not stop the run
            print(f"        FAILED: {exc}", flush=True)
            failed.append(vid)
            with FAILLOG.open("a") as fh:
                fh.write(f"{vid}\t{e.get('title', '')}\t{exc}\n")
            AUDIO.joinpath(f"{vid}.wav").unlink(missing_ok=True)
        time.sleep(args.sleep)

    verb = "re-ran" if args.redo_unpunctuated else "transcribed"
    print(f"\n{verb} {ok}/{len(todo)} in {fmt(time.time() - start_all)}")
    if kept_worse:
        print(f"{len(kept_worse)} kept their original transcription (no improvement):")
        print("  " + " ".join(kept_worse))
    if failed:
        print(f"{len(failed)} failed (see {FAILLOG}); re-run to retry:")
        print("  " + " ".join(failed[:20]))


if __name__ == "__main__":
    main()
