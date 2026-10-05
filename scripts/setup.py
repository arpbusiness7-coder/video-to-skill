"""
Check and set up everything video-to-skill needs. All free, no accounts.

    python3 scripts/setup.py                 # check, as JSON (safe to run any time)
    python3 scripts/setup.py --install       # install yt-dlp, gallery-dl and Whisper (one time)
    python3 scripts/setup.py --prefetch      # download the Whisper model now instead of on first use
    python3 scripts/setup.py --net           # also test whether the needed sites can be reached
    python3 scripts/setup.py --set KEY VALUE # change a setting in config.json

Python packages go into a private environment in your home folder
(~/.video-to-skill-venv), so nothing is installed system-wide and the tool
folder stays small. ffmpeg is the one system program it needs.
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
                    save_config, use_private_env, utf8_streams, venv_python)

PACKAGES = ["yt-dlp[default]", "faster-whisper", "gallery-dl"]
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


def _ffmpeg_fix() -> str:
    return {
        "Darwin": "brew install ffmpeg   (no Homebrew? install it from https://brew.sh first)",
        "Windows": "winget install --id Gyan.FFmpeg -e   (then reopen your terminal / AI tool)",
    }.get(OS, "sudo apt install ffmpeg   (or your system's package manager)")


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

    add("python", "Python 3.9 or newer", sys.version_info >= (3, 9),
        f"{platform.python_version()} at {sys.executable}",
        "Install Python 3.9+ from https://www.python.org/downloads/")
    ff = shutil.which("ffmpeg") and shutil.which("ffprobe")
    add("ffmpeg", "ffmpeg (screenshots and audio)", ff, shutil.which("ffmpeg") or "not found", _ffmpeg_fix())
    add("yt-dlp", "yt-dlp (downloads YouTube and Instagram videos)", _has("yt_dlp"),
        _version("yt-dlp") or "not installed", "python3 scripts/setup.py --install")
    add("whisper", "Whisper / faster-whisper (speech to text, on this computer)", _has("faster_whisper"),
        _version("faster-whisper") or "not installed", "python3 scripts/setup.py --install")
    add("gallery-dl", "gallery-dl (Instagram photo posts and carousels)", _has("gallery_dl"),
        _version("gallery-dl") or "not installed", "python3 scripts/setup.py --install", level="recommended")
    runtime = next((n for n in ("deno", "node", "bun") if shutil.which(n)), None)
    add("js-runtime", "Node.js or Deno (YouTube downloads need one)", runtime,
        f"{runtime} found" if runtime else "none found",
        {"Darwin": "brew install node", "Windows": "winget install --id OpenJS.NodeJS.LTS -e"}.get(
            OS, "sudo apt install nodejs"), level="recommended")

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

def _pip(python: str, extra: list) -> subprocess.CompletedProcess:
    cmd = [python, "-m", "pip", "install", "--upgrade", "--quiet", *extra, *PACKAGES]
    print("  " + " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def install() -> bool:
    print("Setting up a private Python environment for video-to-skill...", file=sys.stderr)
    base = sys.executable
    made = subprocess.run([base, "-m", "venv", str(VENV_DIR)], capture_output=True, text=True)
    python = str(venv_python())
    if made.returncode == 0 and Path(python).exists():
        subprocess.run([python, "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
                       capture_output=True, text=True)
        result = _pip(python, [])
        if result.returncode == 0:
            READY_MARK.write_text("ok\n", encoding="utf-8")
            print("Installed into the private environment.", file=sys.stderr)
            return True
        print(result.stderr[-1500:], file=sys.stderr)
        print("Private environment install failed, trying a per-user install instead.", file=sys.stderr)
    else:
        print("Could not create a private environment (" + (made.stderr.strip().splitlines() or ["?"])[-1]
              + "), using a per-user install instead.", file=sys.stderr)

    # Fallback: install for this user only. Newer systems need the extra flag.
    result = _pip(base, ["--user"])
    if result.returncode != 0 and "externally-managed" in (result.stderr or ""):
        result = _pip(base, ["--user", "--break-system-packages"])
    if result.returncode != 0:
        print(result.stderr[-1500:], file=sys.stderr)
        if "403" in result.stderr or "Tunnel" in result.stderr or "ProxyError" in result.stderr:
            print("\nThe download was blocked by network settings. In the Claude app: Settings > "
                  "Capabilities > network access, allow all domains, then start a new task.", file=sys.stderr)
        return False
    print("Installed for this user.", file=sys.stderr)
    return True


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
