"""
Check and set up everything video-to-skill needs. All free, no accounts.

    python3 scripts/setup.py                 # check, as JSON (safe to run any time)
    python3 scripts/setup.py --install       # install everything it needs (one time, a few minutes)
    python3 scripts/setup.py --prefetch      # download the Whisper model now instead of on first use
    python3 scripts/setup.py --net           # also test whether the needed sites can be reached
    python3 scripts/setup.py --set KEY VALUE # change a setting in config.json

Everything goes into a private folder in your home folder
(~/.video-to-skill-venv): its own up-to-date Python, yt-dlp, gallery-dl,
Whisper, a ready-made ffmpeg and Deno (which YouTube downloads need). Nothing
is installed system-wide, and no Homebrew or admin password is needed.

It uses uv (a free, fast Python installer) to fetch a current Python, because
the Python that comes with macOS is too old for today's yt-dlp.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (CONFIG_FILE, DEFAULT_CONFIG, TOOL_DIR, VENV_DIR, captures_dir, load_config,  # noqa: E402
                    save_config, tools_dir, use_private_env, utf8_streams, venv_python)

# imageio-ffmpeg carries a ready-made ffmpeg for Mac, Windows and Linux.
# deno is the official Deno build on PyPI ("deno>=2": older versions on PyPI
# are an unrelated placeholder). yt-dlp uses Deno for YouTube.
PACKAGES = ["yt-dlp[default]", "faster-whisper", "gallery-dl", "imageio-ffmpeg", "deno>=2"]
PYTHON_VERSION = "3.12"
MIN_PYTHON = (3, 10)   # current yt-dlp needs 3.10+; macOS ships 3.9
BOOTSTRAP_DIR = Path.home() / ".cache" / "video-to-skill" / "bootstrap"
READY_MARK = VENV_DIR / ".video-to-skill-ready"
OS = platform.system()

SITES = {
    "youtube": "https://www.youtube.com",
    "instagram": "https://www.instagram.com",
    "python packages (pypi.org)": "https://pypi.org/simple/pip/",
    "whisper model (huggingface.co)": "https://huggingface.co",
}


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _version(module: str) -> str:
    try:
        from importlib.metadata import version
        return version(module)
    except Exception:
        return ""


def check_site(url: str) -> str:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return f"ok ({response.status})"
    except urllib.error.HTTPError as exc:
        return f"ok ({exc.code})" if exc.code < 500 and exc.code != 403 else f"error ({exc.code})"
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "403" in reason or "Tunnel" in reason or "proxy" in reason.lower():
            return "blocked by network settings"
        return f"unreachable ({reason[:80]})"
    except Exception as exc:  # timeouts and the like
        return f"unreachable ({type(exc).__name__})"


def report(net: bool = False) -> dict:
    config = load_config()
    checks = []

    def add(key, label, ok, detail, fix=None, level="required"):
        checks.append({"id": key, "label": label, "ok": bool(ok), "level": level,
                       "detail": detail, "fix": None if ok else fix})

    fix = "python3 scripts/setup.py --install"
    add("python", "Python 3.10 or newer (setup fetches its own)", sys.version_info >= MIN_PYTHON,
        f"{platform.python_version()} at {sys.executable}", fix)
    add("ffmpeg", "ffmpeg (screenshots and audio)", shutil.which("ffmpeg"),
        shutil.which("ffmpeg") or "not found", fix)
    add("yt-dlp", "yt-dlp (downloads YouTube and Instagram videos)", _has("yt_dlp"),
        _version("yt-dlp") or "not installed", fix)
    add("whisper", "Whisper / faster-whisper (speech to text, on this computer)", _has("faster_whisper"),
        _version("faster-whisper") or "not installed", fix)
    add("gallery-dl", "gallery-dl (Instagram photo posts and carousels)", _has("gallery_dl"),
        _version("gallery-dl") or "not installed", fix, level="recommended")
    runtime = next((n for n in ("deno", "node", "bun") if shutil.which(n)), None)
    add("js-runtime", "Deno or Node.js (YouTube downloads need one)", runtime,
        f"{runtime} found" if runtime else "none found", fix, level="recommended")

    by_id = {c["id"]: c for c in checks}
    required_ok = all(c["ok"] for c in checks if c["level"] == "required")
    out = {
        "ready": required_ok,
        "can_do": {
            "youtube": required_ok and by_id["js-runtime"]["ok"],
            "instagram_reels": required_ok,
            "instagram_photo_posts": required_ok and by_id["gallery-dl"]["ok"],
            "files_on_this_computer": by_id["ffmpeg"]["ok"] and by_id["whisper"]["ok"],
        },
        "private_env": str(VENV_DIR) if READY_MARK.exists() else "not set up",
        "tool_dir": str(TOOL_DIR),
        "captures_dir": str(captures_dir(config)),
        "whisper_model": config.get("whisper_model"),
        "capture_command": f'python3 "{TOOL_DIR / "scripts" / "capture.py"}" <link or file>',
        "checks": checks,
    }
    if net:
        out["network"] = {name: check_site(url) for name, url in SITES.items()}
        if any(v == "blocked by network settings" for v in out["network"].values()):
            out["network_fix"] = ("Some sites are blocked. In the Claude app: Settings > Capabilities > "
                                  "network access, allow all domains (or these sites), then start a new task.")
    return out


# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------

def _step(label: str, cmd: list) -> subprocess.CompletedProcess:
    print(f"  {label}...", file=sys.stderr, flush=True)
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if result.returncode != 0:
        print((result.stderr or result.stdout).strip()[-1500:], file=sys.stderr)
    return result


def _bin(folder: Path, name: str) -> Path:
    if OS == "Windows":
        return folder / "Scripts" / f"{name}.exe"
    return folder / "bin" / name


def _blocked(text: str) -> bool:
    return any(s in (text or "") for s in ("403", "Tunnel", "ProxyError", "proxy"))


def find_uv() -> str | None:
    """uv on the computer already, or a private copy installed with pip."""
    found = shutil.which("uv")
    if found:
        return found
    private = _bin(BOOTSTRAP_DIR, "uv")
    if private.exists():
        return str(private)
    made = _step("Preparing the installer", [sys.executable, "-m", "venv", BOOTSTRAP_DIR])
    if made.returncode == 0:
        got = _step("Getting uv (free Python installer)",
                    [_bin(BOOTSTRAP_DIR, "python"), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "uv"])
        if got.returncode == 0 and private.exists():
            return str(private)
    # Last resort: a per-user install of uv with this Python.
    extra = ["--user"]
    got = _step("Getting uv for this user", [sys.executable, "-m", "pip", "install", "--quiet", *extra, "uv"])
    if got.returncode != 0 and "externally-managed" in (got.stderr or ""):
        got = _step("Getting uv for this user", [sys.executable, "-m", "pip", "install", "--quiet",
                                                *extra, "--break-system-packages", "uv"])
    if got.returncode == 0:
        where = subprocess.run([sys.executable, "-c", "import uv; print(uv.find_uv_bin())"],
                               capture_output=True, text=True)
        if where.returncode == 0 and where.stdout.strip():
            return where.stdout.strip()
    return None


def _venv_is_current() -> bool:
    python = venv_python()
    if not python.exists():
        return False
    check = subprocess.run([str(python), "-c", f"import sys; print(sys.version_info >= {MIN_PYTHON})"],
                           capture_output=True, text=True)
    return check.stdout.strip() == "True"


def link_ffmpeg() -> bool:
    """Put the ready-made ffmpeg where everything can find it by name."""
    where = subprocess.run([str(venv_python()), "-c",
                            "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                           capture_output=True, text=True)
    exe = Path(where.stdout.strip()) if where.returncode == 0 else None
    if not exe or not exe.exists():
        print("  Could not find the ready-made ffmpeg.", file=sys.stderr)
        return False
    tools = tools_dir()
    tools.mkdir(parents=True, exist_ok=True)
    target = tools / ("ffmpeg.exe" if OS == "Windows" else "ffmpeg")
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(exe)
    except OSError:
        shutil.copy2(exe, target)
    try:
        exe.chmod(exe.stat().st_mode | 0o111)
    except OSError:
        pass
    return True


def install() -> bool:
    print("Setting up video-to-skill (free, about 1 GB, a few minutes)...", file=sys.stderr)
    uv = find_uv()
    python = str(venv_python())
    if uv:
        if VENV_DIR.exists() and not _venv_is_current():
            print("  Replacing an older private environment...", file=sys.stderr)
            shutil.rmtree(VENV_DIR, ignore_errors=True)
        if not Path(python).exists():
            made = _step(f"Getting Python {PYTHON_VERSION} for the tool",
                         [uv, "venv", "--python", PYTHON_VERSION, VENV_DIR])
            if made.returncode != 0:
                _hint(made.stderr)
                return False
        result = _step("Installing yt-dlp, gallery-dl, Whisper, ffmpeg and Deno",
                       [uv, "pip", "install", "--python", python, "--upgrade", *PACKAGES])
    elif sys.version_info >= MIN_PYTHON:
        # No uv, but this Python is new enough: plain venv + pip.
        if not Path(python).exists():
            made = _step("Creating the private environment", [sys.executable, "-m", "venv", VENV_DIR])
            if made.returncode != 0:
                return False
        result = _step("Installing yt-dlp, gallery-dl, Whisper, ffmpeg and Deno",
                       [python, "-m", "pip", "install", "--quiet", "--upgrade", *PACKAGES])
    else:
        print("Could not get uv, and this computer's Python is too old to do without it.", file=sys.stderr)
        return False
    if result.returncode != 0:
        _hint(result.stderr)
        return False
    if not link_ffmpeg():
        return False
    READY_MARK.write_text("ok\n", encoding="utf-8")
    print("Installed. Run it again any time to update yt-dlp, which keeps YouTube and Instagram working.",
          file=sys.stderr)
    return True


def _hint(text: str) -> None:
    if _blocked(text):
        print("\nThe download was blocked by network settings. In the Claude app: Settings > "
              "Capabilities > network access, allow all domains, then start a new task.", file=sys.stderr)


def prefetch() -> bool:
    use_private_env()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import transcribe
    config = load_config()
    try:
        transcribe._load_model(config.get("whisper_model") or "small", config.get("models_dir") or "")
    except Exception as exc:
        print(f"Could not download the Whisper model: {exc}", file=sys.stderr)
        return False
    print(f"Whisper '{config.get('whisper_model')}' model is ready.", file=sys.stderr)
    return True


def main() -> None:
    utf8_streams()
    parser = argparse.ArgumentParser(description="Check and set up video-to-skill.")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--prefetch", action="store_true")
    parser.add_argument("--net", action="store_true")
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"))
    args = parser.parse_args()

    if args.install:
        ok = install()
        # Report from inside the new environment so the check reflects it.
        subprocess.run([sys.executable, str(Path(__file__).resolve())])
        sys.exit(0 if ok else 1)
    if args.prefetch:
        sys.exit(0 if prefetch() else 1)
    if args.set:
        key, value = args.set
        if key not in DEFAULT_CONFIG:
            sys.exit(f"Unknown setting {key}. Options: {', '.join(DEFAULT_CONFIG)}")
        typed = int(value) if isinstance(DEFAULT_CONFIG[key], int) else value
        print(json.dumps(save_config({key: typed}), indent=2))
        print(f"Saved to {CONFIG_FILE}", file=sys.stderr)
        return

    use_private_env()
    print(json.dumps(report(net=args.net), indent=2))


if __name__ == "__main__":
    main()
