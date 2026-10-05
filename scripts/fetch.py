"""
Getting the media and the post details, for free.

  YouTube    yt-dlp: details, captions (free and used first), video or audio.
  Instagram  yt-dlp for reels and videos. Photo posts and carousels go through
             gallery-dl, which also saves the post details. Public posts only,
             unless a cookies file is set in config.json.
  File       a video, audio file or image already on this computer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common import (cookies_file, captures_dir, instagram_permalink, log,
                    read_json, write_json, youtube_id)
from media import natural_key
from transcribe import clean_vtt

# Set by capture.py when it is working to a time limit. Downloads that run past
# it stop cleanly; yt-dlp picks up where it left off on the next run.
DEADLINE: float | None = None


class OutOfTime(Exception):
    """The time budget ran out mid-step. Run the same capture again to continue."""


def _timeout() -> float | None:
    if DEADLINE is None:
        return None
    return max(10.0, DEADLINE - time.time())


YT_VIDEO_FORMAT = "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"
YT_AUDIO_FORMAT = "ba/b"
AUTO_TRANSCRIPT_ONLY_SECONDS = 1800  # 30 min: long talks skip the video download


# --------------------------------------------------------------------------
# yt-dlp plumbing
# --------------------------------------------------------------------------

def _js_runtime_args() -> list:
    """yt-dlp needs a JavaScript runtime for YouTube. It looks for Deno by
    default; point it at Node or Bun if that is what is installed."""
    if shutil.which("deno"):
        return []
    for name in ("node", "bun"):
        if shutil.which(name):
            return ["--js-runtimes", name]
    return []


def ytdlp(args: list, cookies: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", *_js_runtime_args()]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    try:
        return subprocess.run(cmd + args, capture_output=True, text=True, timeout=_timeout())
    except subprocess.TimeoutExpired as exc:
        raise OutOfTime("downloading") from exc


def explain(stderr: str, url: str) -> str:
    """Turn a downloader error into one plain sentence."""
    text = stderr.lower()
    last = (stderr.strip().splitlines() or ["unknown error"])[-1][:300]
    if ("tunnel connection failed: 403" in text or "unable to connect to proxy" in text
            or "403 from proxy" in text):
        return ("The network is blocked for this site. In the Claude app, allow it under "
                "Settings > Capabilities (network access), then start a new task.")
    if "no module named yt_dlp" in text or "no module named gallery_dl" in text:
        return "The downloader is not installed. Run `python3 scripts/setup.py --install`."
    if "sign in to confirm" in text:
        return ("YouTube is asking to confirm this is not a bot. Try again later, or set a "
                "cookies file in config.json.")
    if "login required" in text or "rate-limit" in text or "rate limit" in text or "please wait" in text:
        return ("Instagram wants a login or is slowing requests down. Wait 15 to 30 minutes, "
                "or set a cookies file in config.json (see README).")
    if "private" in text:
        return "That post is private. Only public posts work."
    if "not available" in text or "404" in text or "removed" in text or "does not exist" in text:
        return "That post or video is gone, or not available in your region."
    if "age" in text and ("restrict" in text or "confirm your age" in text):
        return "That video is age restricted. It needs a cookies file from a signed-in browser."
    return f"Could not get {url}: {last}"


def _ytdlp_info(url: str, cookies: Path | None = None) -> tuple[dict | None, str]:
    result = ytdlp(["-J", "--skip-download", url], cookies)
    if result.returncode != 0 or not result.stdout.strip():
        return None, result.stderr
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError:
        return None, result.stderr or "yt-dlp returned something unreadable"


def _media_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and not p.name.endswith((".json", ".part", ".ytdl", ".vtt"))),
                  key=natural_key)


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

def _pick_track(tracks: dict, wanted: list[str]) -> str | None:
    for want in wanted:
        if not want:
            continue
        for key in tracks:
            if key == want or key.startswith(want + "-"):
                return key
    return None


def youtube_captions(url: str, info: dict, work: Path) -> tuple[str, str]:
    """(text, kind). Prefers captions a person wrote, then the automatic ones
    in the video's own language, then automatic English."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    original = (info.get("language") or "").split("-")[0]

    track, flag = _pick_track(manual, ["en", original]), "--write-subs"
    if not track and manual:
        track = next(iter(manual))
    if not track:
        flag = "--write-auto-subs"
        track = _pick_track(auto, [f"{original}-orig" if original else "", original, "en-orig", "en"])
    if not track:
        return "", ""

    subs = work / "subs"
    subs.mkdir(parents=True, exist_ok=True)
    ytdlp(["--skip-download", flag, "--sub-langs", track, "--sub-format", "vtt/best",
           "--convert-subs", "vtt", "-o", str(subs / "%(id)s.%(ext)s"), url])
    files = sorted(subs.glob("*.vtt"))
    if not files:
        return "", ""
    text = clean_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
    return text, ("captions" if flag == "--write-subs" else "auto-captions")


def fetch_youtube(url: str, work: Path, frames_mode: str) -> dict:
    info, error = _ytdlp_info(url)
    if info is None:
        raise RuntimeError(explain(error, url))

    description = (info.get("description") or "").strip()
    seconds = float(info.get("duration") or 0)
    result = {
        "source": "youtube",
        "url": info.get("webpage_url") or url,
        "id": "yt-" + (info.get("id") or youtube_id(url) or "unknown"),
        "title": info.get("title") or "YouTube video",
        "author": info.get("uploader") or info.get("channel") or "",
        "caption": description[:3000] + ("..." if len(description) > 3000 else ""),
        "published": info.get("upload_date") or "",
        "duration_seconds": seconds,
        "media": [],
        "transcript": "",
        "transcript_source": "",
        "warnings": [],
    }

    text, kind = youtube_captions(url, info, work)
    if text:
        result["transcript"], result["transcript_source"] = text, kind
        log(f"  captions found ({kind})")
    else:
        log("  no captions, Whisper will transcribe it")

    want_video = frames_mode == "always" or (
        frames_mode == "auto" and (seconds <= AUTO_TRANSCRIPT_ONLY_SECONDS or not text))
    if frames_mode == "never" and text:
        want_video = False

    media_dir = work / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    if want_video:
        pull = ytdlp(["-f", YT_VIDEO_FORMAT, "--merge-output-format", "mp4",
                      "-o", str(media_dir / "video.%(ext)s"), url])
        files = _media_in(media_dir)
        if files:
            result["media"] = files
            return result
        if text:
            result["warnings"].append(
                "Video download failed, so there are no screenshots. " + explain(pull.stderr, url))
            return result
        log("  video download failed, trying audio only")
    elif text:
        if seconds > AUTO_TRANSCRIPT_ONLY_SECONDS and frames_mode == "auto":
            result["warnings"].append(
                f"{seconds / 60:.0f} minute video with captions, so transcript only. "
                "Ask for screenshots (--frames) if the screen matters.")
        return result

    pull = ytdlp(["-f", YT_AUDIO_FORMAT, "-o", str(media_dir / "audio.%(ext)s"), url])
    files = _media_in(media_dir)
    if not files:
        raise RuntimeError(explain(pull.stderr, url))
    result["media"] = files
    return result


# --------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------

def _wait_for_gap(config: dict) -> None:
    """Space Instagram downloads out so a bulk run does not get rate limited."""
    state_file = captures_dir(config) / "_state.json"
    state = read_json(state_file, {})
    gap = float(config.get("instagram_gap_seconds") or 0)
    wait = state.get("last_instagram", 0) + gap - time.time()
    if wait > 0:
        log(f"  waiting {wait:.0f}s between Instagram downloads")
        time.sleep(wait)
    state["last_instagram"] = time.time()
    write_json(state_file, state)


def _gallery_dl(url: str, folder: Path, cookies: Path | None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "gallery_dl", "--write-metadata", "-D", str(folder)]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    try:
        return subprocess.run(cmd + [url], capture_output=True, text=True, timeout=_timeout())
    except subprocess.TimeoutExpired as exc:
        raise OutOfTime("downloading") from exc


def _gallery_meta(folder: Path) -> dict:
    for meta_file in sorted(folder.glob("*.json"), key=natural_key):
        data = read_json(meta_file, {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _instagram_handle(*records: dict) -> str:
    """The account's @username. For Instagram, yt-dlp's `uploader_id` is the
    numeric account id (e.g. 62609148119) and the username is in `channel`,
    so take the first value that isn't just digits, then the display name."""
    for key in ("channel", "uploader_id"):
        for record in records:
            value = str(record.get(key) or "").strip().lstrip("@")
            if value and not value.isdigit():
                return value
    for record in records:
        if record.get("uploader"):
            return str(record["uploader"]).strip()
    return ""


def fetch_instagram(url: str, work: Path, config: dict, caption_hint: str = "") -> dict:
    permalink, code = instagram_permalink(url)
    cookies = cookies_file(config)
    media_dir = work / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    _wait_for_gap(config)

    result = {
        "source": "instagram",
        "url": permalink,
        "id": "ig-" + code,
        "title": "",
        "author": "",
        "caption": "",
        "published": "",
        "duration_seconds": 0.0,
        "media": [],
        "transcript": "",
        "transcript_source": "",
        "warnings": [],
    }

    info, yt_error = _ytdlp_info(permalink, cookies)
    is_single_video = bool(info) and not info.get("entries")
    if info:
        first = (info.get("entries") or [info])[0] or {}
        result["caption"] = (info.get("description") or first.get("description") or "").strip()
        result["author"] = _instagram_handle(info, first)
        stamp = info.get("timestamp") or first.get("timestamp")
        if stamp:
            result["published"] = time.strftime("%Y-%m-%d", time.gmtime(stamp))

    if is_single_video:
        pull = ytdlp(["-f", "bv*+ba/b", "--merge-output-format", "mp4",
                      "-o", str(media_dir / "01.%(ext)s"), permalink], cookies)
        result["media"] = _media_in(media_dir)
        if not result["media"]:
            yt_error = pull.stderr

    if not result["media"]:
        # Photo post, carousel, or yt-dlp could not get it: gallery-dl handles
        # images and mixed carousels, and writes the post details alongside.
        gallery = _gallery_dl(permalink, media_dir, cookies)
        result["media"] = _media_in(media_dir)
        meta = _gallery_meta(media_dir)
        if meta:
            result["caption"] = result["caption"] or (meta.get("description") or "").strip()
            result["author"] = result["author"] or meta.get("username") or ""
            date = str(meta.get("date") or "")
            result["published"] = result["published"] or date[:10]
        if not result["media"] and info and info.get("entries"):
            ytdlp(["-f", "bv*+ba/b", "--merge-output-format", "mp4",
                   "-o", str(media_dir / "%(playlist_index)02d.%(ext)s"), permalink], cookies)
            result["media"] = _media_in(media_dir)
        if not result["media"]:
            reason = explain(gallery.stderr or yt_error, permalink)
            if "no module named gallery_dl" in (gallery.stderr or "").lower() and yt_error:
                reason = explain(yt_error, permalink)
            if caption_hint or result["caption"]:
                result["caption"] = result["caption"] or caption_hint
                result["warnings"].append(f"Media could not be downloaded ({reason}) "
                                          "Working from the caption only.")
                log("  media download failed, caption only")
            else:
                raise RuntimeError(reason)

    result["caption"] = result["caption"] or caption_hint
    first_line = result["caption"].split("\n")[0].strip()
    result["title"] = (first_line[:100] if first_line
                       else f"Post by @{result['author'] or 'unknown'}")
    return result


# --------------------------------------------------------------------------
# A file on this computer
# --------------------------------------------------------------------------

def fetch_file(path_text: str) -> dict:
    path = Path(path_text).expanduser().resolve()
    from common import item_id
    return {
        "source": "file",
        "url": str(path),
        "id": item_id(str(path)),
        "title": path.stem.replace("_", " ").replace("-", " "),
        "author": "",
        "caption": "",
        "published": time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime)),
        "duration_seconds": 0.0,
        "media": [path],
        "transcript": "",
        "transcript_source": "",
        "warnings": [],
    }
