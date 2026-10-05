"""
Capture one video or post: details, transcript, screenshots and contact sheets.

Free and local: yt-dlp / gallery-dl to download, Whisper on this computer to
transcribe, ffmpeg for screenshots. No API keys.

Usage:
    python3 scripts/capture.py <youtube or instagram link, a file path, or a queue id>
        [--frames | --no-frames] [--max-frames N] [--force] [--budget SECONDS] [--fresh]

--budget makes it stop cleanly before a time limit (use it where commands are
cut off after a few minutes, e.g. --budget 150). If it stops early it prints
"state": "partial" and exits with code 2. Run the same command again and it
continues from where it got to.

Output: one JSON object on stdout (progress goes to stderr). The full result,
including the transcript, is also written to captures/_work/result.json, which
notes.py reads when saving, so the transcript never has to be retyped.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (TOOL_DIR, captures_dir, clear_dir, detect_source, item_id, load_config,  # noqa: E402
                    log, read_json, scratch_dir, use_private_env, utf8_streams, work_dir, write_json)


class Partial(Exception):
    def __init__(self, progress: str):
        super().__init__(progress)
        self.progress = progress


def already_captured(target: str, config: dict) -> dict | None:
    try:
        key = item_id(target)
    except RuntimeError:
        return None
    index = read_json(captures_dir(config) / "index.json", {"items": []})
    for entry in index.get("items", []):
        if entry.get("id") == key:
            return entry
    return None


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(TOOL_DIR.resolve()))
    except ValueError:
        return str(path)


class Job:
    """One capture, saved after every step so it can be resumed."""

    def __init__(self, target: str, mode: str, max_frames: int, caption: str, config: dict,
                 budget: float = 0, fresh: bool = False):
        self.config = config
        self.scratch = scratch_dir()
        self.out = work_dir(config)
        self.file = self.scratch / "job.json"
        self.started = time.time()
        self.budget = budget
        self.did_work = False
        saved = read_json(self.file, {})
        if (not fresh and saved.get("target") == target and saved.get("stage") not in (None, "done")):
            self.data = saved
            log(f"Continuing the capture from where it stopped ({saved.get('stage')})")
        else:
            for sub in ("media", "subs", "audio"):
                clear_dir(self.scratch / sub)
            self.data = {"target": target, "mode": mode, "max_frames": max_frames,
                         "caption": caption, "stage": "fetch", "result": {}, "media": [],
                         "chunks": [], "pieces": {}}
        self.save()

    def save(self) -> None:
        write_json(self.file, self.data)

    def time_left(self, need: float) -> None:
        """Stop before starting a step that would not fit in the budget.
        Always lets at least one step run per call, so it keeps moving."""
        if not self.budget or not self.did_work:
            return
        if time.time() - self.started + need > self.budget:
            raise Partial(self.progress())

    def progress(self) -> str:
        stage = self.data["stage"]
        if stage == "transcribe" and self.data["chunks"]:
            done = len(self.data["pieces"])
            return f"transcribed {done} of {len(self.data['chunks'])} parts"
        return {"fetch": "still downloading", "frames": "taking screenshots"}.get(stage, stage)


def _fetch(job: Job) -> None:
    import fetch
    d = job.data
    target = d["target"]
    source = detect_source(target)
    work = job.scratch
    log(f"Getting {source}: {target}")
    if job.budget:
        fetch.DEADLINE = job.started + job.budget
    try:
        if source == "youtube":
            result = fetch.fetch_youtube(target, work, d["mode"])
        elif source == "instagram":
            result = fetch.fetch_instagram(target, work, job.config, d["caption"])
        else:
            result = fetch.fetch_file(target)
    except fetch.OutOfTime as exc:
        raise Partial("still downloading") from exc
    finally:
        fetch.DEADLINE = None
    d["media"] = [str(p) for p in result.pop("media")]
    d["result"] = result
    d["stage"] = "transcribe"
    job.did_work = True
    job.save()


def _transcribe(job: Job) -> None:
    import media
    import transcribe
    d, result = job.data, job.data["result"]
    if result.get("transcript"):
        d["stage"] = "frames"
        job.save()
        return
    groups = media.classify([Path(p) for p in d["media"]])
    spoken = [p for p in groups["video"] + groups["audio"] if media.has_stream(p, "a")]

    if not d["chunks"]:
        for n, item in enumerate(spoken, start=1):
            audio = media.extract_audio(item, job.scratch / "audio" / f"a{n}.wav")
            for chunk in media.split_audio(audio, job.scratch / "audio" / f"a{n}_parts"):
                d["chunks"].append({**chunk, "file": item.name, "n": n})
        job.save()

    model = job.config.get("whisper_model") or "small"
    for i, chunk in enumerate(d["chunks"]):
        key = str(i)
        if key in d["pieces"]:
            continue
        job.time_left(need=d.get("seconds_per_part", 60))
        log(f"Transcribing part {i + 1} of {len(d['chunks'])} with Whisper (free, on this computer)...")
        began = time.time()
        heard = transcribe.whisper(Path(chunk["path"]), model, job.config.get("language") or "",
                                   job.config.get("models_dir") or "")
        d["pieces"][key] = {
            "text": heard["text"], "language": heard["language"],
            "segments": [{**s, "start": round(s["start"] + chunk["offset"], 1),
                          "end": round(s["end"] + chunk["offset"], 1), "file": chunk["file"]}
                         for s in heard["segments"]]}
        d["seconds_per_part"] = round((time.time() - began) * 1.2 + 5, 1)
        job.did_work = True
        job.save()

    # Put the parts back together, one block per video.
    texts, segments = {}, []
    for i, chunk in enumerate(d["chunks"]):
        piece = d["pieces"].get(str(i), {})
        if piece.get("text"):
            texts.setdefault(chunk["n"], []).append(piece["text"])
            result["language"] = piece.get("language") or result.get("language", "")
        segments.extend(piece.get("segments", []))
    blocks = []
    for n in sorted(texts):
        label = f"[Video {n}]\n" if len(spoken) > 1 else ""
        blocks.append(label + " ".join(texts[n]))
    result["transcript"] = "\n\n".join(blocks)
    if blocks:
        result["transcript_source"] = "whisper"
    elif groups["video"] or groups["audio"]:
        result["transcript_source"] = "none (no speech)"
        log("  no speech found, the screenshots and caption are the whole story")
    else:
        result["transcript_source"] = "none (photo post)" if groups["image"] else "none"
    d["segments"] = segments
    d["stage"] = "frames"
    job.save()


def _frames(job: Job) -> dict:
    import media
    d, result = job.data, job.data["result"]
    job.time_left(need=20)
    groups = media.classify([Path(p) for p in d["media"]])
    videos, images = groups["video"], groups["image"]
    frames_dir, sheets_dir = job.out / "frames", job.out / "sheets"
    clear_dir(frames_dir)
    clear_dir(sheets_dir)

    total_seconds = sum(media.duration(v) for v in videos)
    result["duration_seconds"] = round(total_seconds or result.get("duration_seconds") or 0, 1)
    frames: list[dict] = []
    if d["mode"] != "never" or not result.get("transcript"):
        budget = media.frame_budget(total_seconds or 1, d["max_frames"])
        index = 1
        for item in videos:
            if not media.has_stream(item, "v"):
                continue
            share = media.duration(item) / total_seconds if total_seconds else 1 / len(videos)
            count = max(1, round(budget * share)) if len(videos) > 1 else budget
            got = media.extract_frames(item, frames_dir, count, start_index=index)
            frames.extend(got)
            index += len(got)
        for image in images:
            out = media.image_to_frame(image, frames_dir / f"frame_{index:02d}.jpg")
            if out:
                frames.append({"path": out, "at": None, "from": image.name})
                index += 1

    sheets = media.contact_sheets([f["path"] for f in frames], sheets_dir, job.scratch / "tiles")
    timed = [f for f in frames if f["at"] is not None]
    interval = round(result["duration_seconds"] / len(timed), 1) if timed and result["duration_seconds"] else None
    result.update({
        "frames": [_rel(f["path"]) for f in frames],
        "frame_times": [f["at"] for f in frames],
        "frame_interval_seconds": interval,
        "sheets": [_rel(s) for s in sheets],
        "sheet_layout": ("6 per sheet, read left to right, top to bottom, in frame order. "
                         "Plain black slots after the last screenshot are empty padding."),
        "media_counts": {k: len(v) for k, v in groups.items()},
        "tool_dir": str(TOOL_DIR),
        "captured": time.strftime("%Y-%m-%d"),
    })
    result.setdefault("warnings", [])
    if interval and interval > 120:
        result["warnings"].append(
            f"One screenshot every {interval / 60:.0f} minutes is too sparse to read the screen. "
            "Use --max-frames to take more.")
    write_json(job.out / "result.json", dict(result, transcript_segments=d.get("segments", [])))
    d["stage"] = "done"
    job.save()
    return result


def capture(target: str, frames_mode: str = "auto", max_frames: int = 0, caption_hint: str = "",
            config: dict | None = None, budget: float = 0, fresh: bool = False) -> dict:
    """Runs (or resumes) a capture. Raises Partial if the budget runs out."""
    import media
    config = config or load_config()
    detect_source(target)
    media.require_ffmpeg()
    job = Job(target, frames_mode, max_frames, caption_hint, config, budget, fresh)
    write_json(job.out / "status.json", {"state": "running", "target": target})
    try:
        if job.data["stage"] == "fetch":
            _fetch(job)
        if job.data["stage"] == "transcribe":
            _transcribe(job)
        result = _frames(job)
    except Partial as part:
        write_json(job.out / "status.json", {"state": "partial", "target": target, "progress": part.progress})
        raise
    except Exception as exc:
        write_json(job.out / "status.json", {"state": "error", "target": target, "error": str(exc)})
        raise
    write_json(job.out / "status.json", {"state": "done", "target": target})
    return result


def main() -> None:
    utf8_streams()
    use_private_env()
    parser = argparse.ArgumentParser(description="Capture a video or post for later.")
    parser.add_argument("target", help="YouTube or Instagram link, a file path, or a queue id like ig-ABC123")
    parser.add_argument("--frames", action="store_true", help="always take screenshots, even on long videos")
    parser.add_argument("--no-frames", action="store_true", help="transcript only")
    parser.add_argument("--max-frames", type=int, default=0, help="override how many screenshots")
    parser.add_argument("--caption", default="", help="caption text to fall back on")
    parser.add_argument("--force", action="store_true", help="capture again even if already saved")
    parser.add_argument("--budget", type=float, default=0,
                        help="stop cleanly after about this many seconds; run again to continue")
    parser.add_argument("--fresh", action="store_true", help="start over instead of continuing")
    args = parser.parse_args()
    if args.frames and args.no_frames:
        parser.error("--frames and --no-frames contradict each other")
    mode = "never" if args.no_frames else ("always" if args.frames else "auto")
    config = load_config()

    # A bulk-queue id (from saved.py next) stands in for its link and saved caption.
    if args.target.startswith(("ig-", "yt-")) and not Path(args.target).exists():
        import saved
        item = saved.lookup(args.target)
        if not item:
            print(f"ERROR: {args.target} is not in the queue", file=sys.stderr)
            sys.exit(1)
        args.target = item["url"]
        args.caption = args.caption or item.get("caption") or ""

    if not args.force:
        existing = already_captured(args.target, config)
        if existing:
            print(json.dumps({"already_captured": True, **existing}, indent=2, ensure_ascii=False))
            return

    try:
        result = capture(args.target, mode, args.max_frames, args.caption, config, args.budget, args.fresh)
    except Partial as part:
        print(json.dumps({"state": "partial", "progress": part.progress,
                          "next": "run the same command again to continue"}, indent=2))
        sys.exit(2)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"state": "done", **result}, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
