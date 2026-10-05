"""
ffmpeg work: duration, audio, still frames and contact sheets.

Contact sheets matter in the Claude app: each image has to be copied across
before Claude can look at it, so 12 frames become 2 sheets of 6. The single
frames are still kept, so any frame with small text can be opened on its own.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from common import log

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}

SECONDS_PER_FRAME = 5
MAX_FRAMES_SHORT = 12   # videos up to 3 minutes
MAX_FRAMES_LONG = 24    # longer videos
LONG_VIDEO_SECONDS = 180
FRAME_LONG_SIDE = 960   # single frames, readable when opened on their own
PER_SHEET = 6


def natural_key(path: Path):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)]


def require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found. Run `python3 scripts/setup.py --install`, which adds a free ready-made copy.")


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _use_ffprobe() -> bool:
    # ffprobe is used when the computer has it. The ready-made ffmpeg that
    # setup.py installs comes without it, so everything also works from
    # ffmpeg's own description of the file.
    return bool(shutil.which("ffprobe")) and not os.environ.get("VTS_NO_FFPROBE")


_PROBES: dict = {}


def probe(path: Path) -> dict:
    """{"duration", "audio", "video", "width", "height"} for a media file."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime, path.stat().st_size)
    except OSError:
        return {"duration": 0.0, "audio": False, "video": False, "width": 0, "height": 0}
    if key in _PROBES:
        return _PROBES[key]
    info = {"duration": 0.0, "audio": False, "video": False, "width": 0, "height": 0}
    if _use_ffprobe():
        result = _run(["ffprobe", "-v", "error", "-show_entries",
                       "format=duration:stream=codec_type,width,height", "-of", "json", str(path)])
        try:
            data = json.loads(result.stdout or "{}")
            info["duration"] = float((data.get("format") or {}).get("duration") or 0)
            for stream in data.get("streams") or []:
                if stream.get("codec_type") == "audio":
                    info["audio"] = True
                elif stream.get("codec_type") == "video" and not info["video"]:
                    info.update(video=True, width=int(stream.get("width") or 0),
                                height=int(stream.get("height") or 0))
        except (ValueError, TypeError):
            pass
    else:
        text = _run(["ffmpeg", "-hide_banner", "-i", str(path)]).stderr
        found = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if found:
            h, m, sec = found.groups()
            info["duration"] = int(h) * 3600 + int(m) * 60 + float(sec)
        for line in text.splitlines():
            if not re.search(r"Stream #\d+:\d+", line):
                continue
            if ": Audio:" in line:
                info["audio"] = True
            elif ": Video:" in line and not info["video"]:
                info["video"] = True
                dims = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line.split(": Video:", 1)[1])
                if dims:
                    info["width"], info["height"] = int(dims.group(1)), int(dims.group(2))
    _PROBES[key] = info
    return info


def duration(path: Path) -> float:
    return probe(path)["duration"]


def has_stream(path: Path, kind: str) -> bool:
    """kind is "a" (audio) or "v" (video)."""
    return probe(path)["audio" if kind == "a" else "video"]


def size(path: Path) -> tuple[int, int]:
    info = probe(path)
    return info["width"], info["height"]


def extract_audio(video: Path, destination: Path) -> Path:
    """16 kHz mono WAV, which is what Whisper wants anyway."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                   "-vn", "-ac", "1", "-ar", "16000", str(destination)])
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"ffmpeg could not pull the audio out:\n{result.stderr[-400:]}")
    return destination


CHUNK_SECONDS = 120


def split_audio(audio: Path, out_dir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[dict]:
    """Cut long audio into pieces so transcription can be done a few minutes
    at a time and picked up again if a run stops partway."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = duration(audio)
    if total <= chunk_seconds * 1.1:
        return [{"path": str(audio), "offset": 0.0}]
    stem = audio.stem
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-f", "segment",
          "-segment_time", str(chunk_seconds), "-c", "copy", str(out_dir / f"{stem}_%03d.wav")])
    pieces = sorted(out_dir.glob(f"{stem}_*.wav"), key=natural_key)
    if not pieces:
        return [{"path": str(audio), "offset": 0.0}]
    chunks, offset = [], 0.0
    for piece in pieces:
        chunks.append({"path": str(piece), "offset": round(offset, 2)})
        offset += duration(piece)
    return chunks


def frame_budget(seconds: float, override: int = 0) -> int:
    if override:
        return max(1, override)
    cap = MAX_FRAMES_LONG if seconds > LONG_VIDEO_SECONDS else MAX_FRAMES_SHORT
    return min(cap, max(1, math.ceil(seconds / SECONDS_PER_FRAME)))


_SCALE = (f"scale='if(gt(iw,ih),{FRAME_LONG_SIDE},-2)':"
          f"'if(gt(iw,ih),-2,{FRAME_LONG_SIDE})'")


def extract_frames(video: Path, out_dir: Path, count: int, prefix: str = "frame",
                   start_index: int = 1) -> list[dict]:
    """Evenly spaced stills, each taken from the middle of its slice of the video."""
    out_dir.mkdir(parents=True, exist_ok=True)
    seconds = duration(video)
    if seconds <= 0:
        count, seconds = 1, 0.0
    frames = []
    for i in range(count):
        at = seconds * (i + 0.5) / count if seconds else 0.0
        out = out_dir / f"{prefix}_{start_index + i:02d}.jpg"
        result = _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
                       "-frames:v", "1", "-vf", _SCALE, "-q:v", "3", str(out)])
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            frames.append({"path": out, "at": round(at, 1), "from": video.name})
        else:
            log(f"  frame at {at:.1f}s failed, skipped")
    return frames


def image_to_frame(image: Path, out: Path) -> Path | None:
    """Normalise a post image (png, webp, jpg) to a jpg frame of the same size as video frames."""
    result = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(image),
                   "-frames:v", "1", "-vf", _SCALE, "-q:v", "3", str(out)])
    if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
        return out
    log(f"  could not read image {image.name}, skipped")
    return None


def _layout(width: int, height: int) -> tuple[int, int, int, int]:
    """(cell_w, cell_h, columns, rows) for 6 frames per sheet.

    Upright reels go 3 across by 2 down, wide video 2 across by 3 down, so a
    sheet stays roughly square and under the size an image viewer shrinks."""
    if height > width * 1.15:
        return 360, 640, 3, 2
    if width > height * 1.15:
        return 640, 360, 2, 3
    return 480, 480, 3, 2


def contact_sheets(frames: list[Path], out_dir: Path, scratch: Path | None = None) -> list[Path]:
    """Tile frames into sheets of 6, read left to right, top to bottom.
    Working files go in `scratch` so only the finished sheets land in out_dir."""
    if not frames:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = scratch or out_dir
    scratch.mkdir(parents=True, exist_ok=True)
    width, height = size(frames[0])
    cell_w, cell_h, cols, rows = _layout(width or 9, height or 16)
    fit = (f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
           f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color=black")
    blank = scratch / "_blank.jpg"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
          "-i", f"color=c=black:s={cell_w}x{cell_h}", "-frames:v", "1", str(blank)])

    sheets = []
    for sheet_no, start in enumerate(range(0, len(frames), PER_SHEET), start=1):
        batch = frames[start:start + PER_SHEET]
        staging = scratch / f"_tile{sheet_no}"
        staging.mkdir(parents=True, exist_ok=True)
        # Every tile is resized to exactly the same size first. If ffmpeg meets
        # a size change part-way through the tiles it restarts the sheet, which
        # left most of it blank (partly filled sheets, mixed-shape carousels).
        for slot in range(PER_SHEET):
            tile = staging / f"t_{slot + 1:02d}.jpg"
            made = False
            if slot < len(batch):
                fitted = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(batch[slot]),
                               "-frames:v", "1", "-vf", fit, "-q:v", "3", str(tile)])
                made = fitted.returncode == 0 and tile.exists() and tile.stat().st_size > 0
            if not made:
                shutil.copyfile(blank, tile)
        out = out_dir / f"sheet_{sheet_no}.jpg"
        result = _run(["ffmpeg", "-y", "-loglevel", "error", "-start_number", "1",
                       "-i", str(staging / "t_%02d.jpg"), "-frames:v", "1",
                       "-vf", f"tile={cols}x{rows}:padding=4:color=white", "-q:v", "3", str(out)])
        if result.returncode == 0 and out.exists():
            sheets.append(out)
        else:
            log(f"  contact sheet {sheet_no} failed: {result.stderr.strip()[-200:]}")
    return sheets


def classify(paths: list[Path]) -> dict:
    groups = {"video": [], "audio": [], "image": []}
    for path in sorted(paths, key=natural_key):
        ext = path.suffix.lower()
        if ext in VIDEO_EXT:
            groups["video"].append(path)
        elif ext in AUDIO_EXT:
            groups["audio"].append(path)
        elif ext in IMAGE_EXT:
            groups["image"].append(path)
    return groups
