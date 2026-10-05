"""
Speech to text, free and on this computer, using Whisper (faster-whisper).

The model downloads once (about 460 MB for "small") and is reused after that.
Also cleans YouTube caption files, which are free and used first when a video
has them.
"""
from __future__ import annotations

import re
from pathlib import Path

from common import log

_MODEL = None
_MODEL_NAME = None


def _load_model(name: str, models_dir: str = ""):
    global _MODEL, _MODEL_NAME
    if _MODEL is not None and _MODEL_NAME == name:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Whisper is not installed yet. Run `python3 scripts/setup.py --install` "
            "(free, one time).") from exc
    log(f"  loading Whisper '{name}' (first run downloads it once)...")
    _MODEL = WhisperModel(name, device="auto", compute_type="int8",
                          download_root=models_dir or None)
    _MODEL_NAME = name
    return _MODEL


def _read_wav(path: Path):
    """The 16 kHz mono WAV that media.extract_audio writes, as the float array
    Whisper wants. Handing Whisper the samples directly means it never has to
    decode the file itself, which depends on the PyAV library and broke when
    PyAV 19 changed its open() call. Returns None for anything else."""
    import wave
    try:
        import numpy as np
        with wave.open(str(path), "rb") as handle:
            if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 16000):
                return None
            raw = handle.readframes(handle.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except (wave.Error, OSError, ImportError, EOFError):
        return None


def whisper(audio: Path, model_name: str = "small", language: str = "",
            models_dir: str = "") -> dict:
    """Returns {"text", "language", "segments"}. Paragraph breaks go where the
    speaker paused, so long transcripts stay readable."""
    model = _load_model(model_name, models_dir)
    samples = _read_wav(Path(audio))
    segments, info = model.transcribe(
        samples if samples is not None else str(audio),
        language=language or None, vad_filter=True, beam_size=5)
    parts, kept, last_end, run = [], [], 0.0, 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if parts and (seg.start - last_end > 1.5 or run >= 6):
            parts.append("\n\n")
            run = 0
        elif parts:
            parts.append(" ")
        parts.append(text)
        kept.append({"start": round(seg.start, 1), "end": round(seg.end, 1), "text": text})
        last_end, run = seg.end, run + 1
    return {
        "text": "".join(parts).strip(),
        "language": getattr(info, "language", "") or "",
        "segments": kept,
    }


# --------------------------------------------------------------------------
# YouTube caption files (.vtt)
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_TIMING = re.compile(r"-->")


def clean_vtt(text: str) -> str:
    """Plain text from a WebVTT caption file.

    YouTube's automatic captions scroll: each cue repeats the line before it
    and adds a new one, and lines grow word by word. Keep each line once, in
    its fullest form.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if (not line or line == "WEBVTT" or _TIMING.search(line)
                or line.startswith(("Kind:", "Language:", "NOTE", "STYLE"))
                or line.isdigit()):
            continue
        line = _TAG.sub("", line)
        line = re.sub(r"\s+", " ", line).replace("&nbsp;", " ").replace("&amp;", "&").strip()
        if line:
            lines.append(line)

    kept: list[str] = []
    for line in lines:
        if kept and line == kept[-1]:
            continue
        if kept and line.startswith(kept[-1]):
            kept[-1] = line          # same line, grown by a few words
            continue
        if len(kept) >= 2 and line == kept[-2]:
            continue                  # scrolled-up repeat
        if kept and kept[-1].startswith(line):
            continue                  # shorter echo of a line already kept
        kept.append(line)

    # Join into sentences, then paragraphs of a sensible size.
    text = " ".join(kept)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paragraphs, current = [], []
    for sentence in sentences:
        current.append(sentence)
        if sum(len(s) for s in current) > 600:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    # Auto captions often have no punctuation at all, which would leave one
    # giant paragraph. Break those into chunks of about 120 words.
    out = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if len(paragraph) <= 900:
            out.append(paragraph)
            continue
        for i in range(0, len(words), 120):
            out.append(" ".join(words[i:i + 120]))
    return "\n\n".join(p.strip() for p in out if p.strip())
