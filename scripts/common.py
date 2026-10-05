"""
Shared helpers: paths, config, the private Python environment, URL handling.

Everything here is free and local. No API keys, no paid services.

Folder layout (the "tool folder" is the folder that holds scripts/):

    video-to-skill/
      scripts/            the code
      captures/           your notes (default; change with config.json)
        index.md          list of everything captured, newest first
        index.json        same list, for the scripts
        _queue.json       bulk-mode queue (Instagram saved posts)
        _work/            screenshots, contact sheets and result.json for the
                          current capture, overwritten each run
      config.json         optional settings (see DEFAULT_CONFIG)

    Downloaded video and audio go to ~/.cache/video-to-skill/work instead,
    outside the tool folder.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TOOL_DIR / "scripts"
CONFIG_FILE = TOOL_DIR / "config.json"

# The private Python environment lives in the user's home folder, never inside
# the tool folder, so the tool folder stays small and only holds code and notes.
VENV_DIR = Path(os.environ.get("VTS_VENV", Path.home() / ".video-to-skill-venv"))

DEFAULT_CONFIG = {
    # Where notes are saved. Relative paths are relative to the tool folder.
    "captures_dir": "captures",
    # Whisper model size: tiny, base, small, medium, large-v3, large-v3-turbo.
    # "small" is the sweet spot for speed and accuracy on a laptop CPU.
    "whisper_model": "small",
    # Leave empty to auto-detect the spoken language.
    "language": "",
    # Optional cookies.txt for Instagram, only if public posts start asking for
    # a login. Relative paths are relative to the tool folder.
    "cookies_file": "",
    # Minimum seconds between Instagram downloads, so a bulk run does not get
    # rate limited.
    "instagram_gap_seconds": 20,
    # Optional folder for the Whisper model files. Empty uses the default cache.
    "models_dir": "",
}


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Private environment
# --------------------------------------------------------------------------

def venv_bin() -> Path:
    return VENV_DIR / ("Scripts" if platform.system() == "Windows" else "bin")


def venv_python() -> Path:
    return venv_bin() / ("python.exe" if platform.system() == "Windows" else "python")


def tools_dir() -> Path:
    """Ready-made helper programs (ffmpeg) that setup.py puts next to the
    private environment, so nothing has to be installed system-wide."""
    return VENV_DIR / "tools"


def add_tools_to_path() -> None:
    """Let this process, yt-dlp and ffmpeg calls find the private ffmpeg and
    Deno before anything else on the computer."""
    extra = [str(p) for p in (tools_dir(), venv_bin()) if p.is_dir()]
    current = os.environ.get("PATH", "").split(os.pathsep)
    missing = [p for p in extra if p not in current]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + current)


def use_private_env() -> None:
    """Re-run the current script inside the private environment if it exists.

    Means callers can always type plain `python3 scripts/x.py` and still get
    yt-dlp and Whisper, without activating anything.
    """
    add_tools_to_path()
    target = venv_python()
    ready = VENV_DIR / ".video-to-skill-ready"   # written by setup.py after a good install
    if os.environ.get("VTS_IN_VENV") or not target.exists() or not ready.exists():
        return
    # Compare environments, not interpreter files: a venv's python is usually a
    # link to the system one, so the files resolve to the same place.
    try:
        if Path(sys.prefix).resolve() == VENV_DIR.resolve():
            return
    except OSError:
        return
    env = dict(os.environ, VTS_IN_VENV="1")
    result = subprocess.run([str(target), *sys.argv], env=env)
    sys.exit(result.returncode)


# --------------------------------------------------------------------------
# Config and paths
# --------------------------------------------------------------------------

def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.is_file():
        try:
            config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            log(f"  config.json could not be read ({exc}), using defaults")
    return config


def save_config(updates: dict) -> dict:
    current = {}
    if CONFIG_FILE.is_file():
        try:
            current = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current = {}
    current.update(updates)
    CONFIG_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return load_config()


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else TOOL_DIR / path


def captures_dir(config: dict | None = None) -> Path:
    config = config or load_config()
    path = _resolve(config.get("captures_dir") or "captures")
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir(config: dict | None = None) -> Path:
    path = captures_dir(config) / "_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scratch_dir() -> Path:
    """Big temporary files (downloaded video, audio, caption files) live here,
    outside the tool folder, so they never clutter it and can always be
    cleared. Only screenshots, contact sheets and text go in captures/_work."""
    path = Path(os.environ.get("VTS_SCRATCH", Path.home() / ".cache" / "video-to-skill" / "work"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cookies_file(config: dict | None = None) -> Path | None:
    config = config or load_config()
    raw = config.get("cookies_file") or ""
    if not raw:
        return None
    path = _resolve(raw)
    return path if path.is_file() else None


def clear_dir(path: Path) -> None:
    """Empty a scratch folder. Where deleting is not allowed (some sandboxes),
    files are left and simply overwritten by the next run instead."""
    if not path.is_dir():
        return
    for child in path.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

INSTAGRAM_POST = re.compile(
    r"^https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(p|reels?|tv)/([\w-]+)", re.I)
YOUTUBE = re.compile(r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/", re.I)
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})")


def detect_source(target: str) -> str:
    """youtube, instagram or file. Anything else raises a plain error."""
    if YOUTUBE.match(target):
        return "youtube"
    if INSTAGRAM_POST.match(target):
        return "instagram"
    if Path(target).expanduser().is_file():
        return "file"
    if re.match(r"^https?://", target):
        raise RuntimeError(
            f"Not a YouTube or Instagram post link: {target}\n"
            "This handles YouTube, Instagram reels/posts and video files on disk.")
    raise RuntimeError(f"No such file: {target}")


def instagram_permalink(url: str) -> tuple[str, str]:
    """(clean permalink, shortcode). Reels and posts both work at /p/<code>/,
    but keep reels as /reel/ since that is what people recognise."""
    match = INSTAGRAM_POST.match(url.strip())
    if not match:
        raise RuntimeError(f"Not an Instagram post link: {url}")
    kind, code = match.group(1).lower(), match.group(2)
    kind = "reel" if kind.startswith("reel") else kind
    return f"https://www.instagram.com/{kind}/{code}/", code


def youtube_id(url: str) -> str | None:
    match = YOUTUBE_ID.search(url)
    return match.group(1) if match else None


def item_id(target: str) -> str:
    """Stable id for a capture, used to spot duplicates."""
    source = detect_source(target)
    if source == "instagram":
        return "ig-" + instagram_permalink(target)[1]
    if source == "youtube":
        return "yt-" + (youtube_id(target) or re.sub(r"\W+", "-", target)[-40:])
    return "file-" + slugify(Path(target).stem)


def slugify(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text[:limit].rstrip("-")) or "capture"


def read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data) -> None:
    # Plain overwrite rather than write-then-rename: some sandboxes refuse the
    # rename-over-existing step, and nothing here needs atomic writes.
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
