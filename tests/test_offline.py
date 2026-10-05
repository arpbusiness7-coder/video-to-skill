"""
Offline tests: everything except real downloads and real Whisper.

    python3 -m unittest discover -s tests -v

Each test works on a throwaway copy of the tool, so your real captures are
never touched. Needs ffmpeg.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

REAL_TOOL = Path(__file__).resolve().parent.parent


def make_tool_copy() -> Path:
    root = Path(tempfile.mkdtemp(prefix="vts_test_")) / "video-to-skill"
    shutil.copytree(REAL_TOOL / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return root


def run(tool: Path, *args, check=True) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "VTS_IN_VENV": "1",
           "HOME": str(tool.parent), "VTS_SCRATCH": str(tool.parent / "scratch")}
    result = subprocess.run([sys.executable, *args], cwd=tool, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"{args} failed:\n{result.stdout}\n{result.stderr}")
    return result


def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def make_video(path: Path, width: int, height: int, seconds: int, audio: bool) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={width}x{height}:rate=10"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    ffmpeg(*args, "-pix_fmt", "yuv420p", str(path))
    return path


def load_modules(tool: Path):
    os.environ["VTS_SCRATCH"] = str(tool.parent / "scratch")
    sys.path.insert(0, str(tool / "scripts"))
    for name in ("common", "media", "transcribe", "fetch", "capture", "notes", "saved"):
        sys.modules.pop(name, None)
    modules = {name: importlib.import_module(name)
               for name in ("common", "media", "transcribe", "fetch", "capture", "saved")}
    sys.path.pop(0)
    return modules


class Urls(unittest.TestCase):
    def setUp(self):
        self.m = load_modules(make_tool_copy())["common"]

    def test_detect(self):
        d = self.m.detect_source
        self.assertEqual(d("https://www.instagram.com/reel/ABC_1-x/"), "instagram")
        self.assertEqual(d("https://instagram.com/p/XYZ123/?igsh=abc"), "instagram")
        self.assertEqual(d("https://www.instagram.com/someuser/reel/ABC/"), "instagram")
        self.assertEqual(d("https://youtu.be/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(d("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "youtube")
        for bad in ("https://www.tiktok.com/@a/video/1", "https://www.instagram.com/stories/x/1/",
                    "https://www.instagram.com/someuser/"):
            with self.assertRaises(RuntimeError):
                d(bad)

    def test_ids(self):
        self.assertEqual(self.m.item_id("https://www.instagram.com/reels/ABC/"), "ig-ABC")
        self.assertEqual(self.m.item_id("https://www.instagram.com/p/ABC/"), "ig-ABC")
        self.assertEqual(self.m.item_id("https://youtu.be/dQw4w9WgXcQ?t=3"), "yt-dQw4w9WgXcQ")
        self.assertEqual(self.m.instagram_permalink("https://instagram.com/reels/ABC/?x=1")[0],
                         "https://www.instagram.com/reel/ABC/")


class Captions(unittest.TestCase):
    def setUp(self):
        self.t = load_modules(make_tool_copy())["transcribe"]

    def test_rolling_auto_captions(self):
        vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000 align:start position:0%
so today I'm going to show<00:00:00.500><c> you</c>

00:00:02.000 --> 00:00:02.010
so today I'm going to show you

00:00:02.010 --> 00:00:04.000
so today I'm going to show you
how to set up<00:00:02.500><c> the</c><c> tool</c>

00:00:04.000 --> 00:00:04.010
how to set up the tool

00:00:04.010 --> 00:00:06.000
how to set up the tool
in under a minute
"""
        self.assertEqual(self.t.clean_vtt(vtt),
                         "so today I'm going to show you how to set up the tool in under a minute")

    def test_long_unpunctuated_text_is_chunked(self):
        body = "\n\n".join(f"00:00:{i:02d}.000 --> 00:00:{i:02d}.900\nword{i} " + "filler " * 20
                           for i in range(40))
        text = self.t.clean_vtt("WEBVTT\n\n" + body)
        self.assertGreater(text.count("\n\n"), 3)
        self.assertIn("word39", text)


def brightness(image: Path, x: int, y: int, w: int, h: int) -> float:
    """Average brightness (0-255) of one region of an image."""
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(image), "-vf",
                          f"crop={w}:{h}:{x}:{y},scale=1:1,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return out[0]


def cell_brightness(sheet: Path, index: int, cell_w: int, cell_h: int, cols: int) -> float:
    col, row = index % cols, index // cols
    return brightness(sheet, col * (cell_w + 4) + 20, row * (cell_h + 4) + 20, cell_w - 40, cell_h - 40)


class Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = make_tool_copy()
        cls.m = load_modules(cls.tool)["media"]
        cls.tmp = Path(tempfile.mkdtemp())

    def test_upright_reel_frames_and_sheets(self):
        video = make_video(self.tmp / "reel.mp4", 1080, 1920, 30, audio=True)
        self.assertTrue(self.m.has_stream(video, "a"))
        count = self.m.frame_budget(self.m.duration(video))
        self.assertEqual(count, 6)
        frames = self.m.extract_frames(video, self.tmp / "frames_r", count)
        self.assertEqual(len(frames), 6)
        self.assertEqual(self.m.size(frames[0]["path"]), (540, 960))
        sheets = self.m.contact_sheets([f["path"] for f in frames], self.tmp / "sheets_r")
        self.assertEqual(len(sheets), 1)
        w, h = self.m.size(sheets[0])
        self.assertTrue(1000 < w < 1200 and 1200 < h < 1400, (w, h))
        for i in range(6):   # every cell holds a real picture, not a blank
            self.assertGreater(cell_brightness(sheets[0], i, 360, 640, 3), 40, f"cell {i}")

    def test_wide_video_partial_last_sheet(self):
        video = make_video(self.tmp / "wide.mp4", 1280, 720, 40, audio=False)
        self.assertFalse(self.m.has_stream(video, "a"))
        frames = self.m.extract_frames(video, self.tmp / "frames_w", 8)
        sheets = self.m.contact_sheets([f["path"] for f in frames], self.tmp / "sheets_w")
        self.assertEqual(len(sheets), 2)          # 6 + 2 (padded with blanks)
        w, h = self.m.size(sheets[1])
        self.assertTrue(w > h * 0.9 and h > 1000, (w, h))
        self.assertGreater(cell_brightness(sheets[1], 0, 640, 360, 2), 40)   # real frames...
        self.assertGreater(cell_brightness(sheets[1], 1, 640, 360, 2), 40)
        self.assertLess(cell_brightness(sheets[1], 2, 640, 360, 2), 10)      # ...then blanks

    def test_mixed_shapes_like_a_carousel(self):
        upright = make_video(self.tmp / "u.mp4", 720, 1280, 6, audio=False)
        wide = make_video(self.tmp / "w.mp4", 1280, 720, 6, audio=False)
        frames = (self.m.extract_frames(upright, self.tmp / "mixed", 2, prefix="a")
                  + self.m.extract_frames(wide, self.tmp / "mixed", 2, prefix="b"))
        sheet = self.m.contact_sheets([f["path"] for f in frames], self.tmp / "sheets_m")[0]
        for i in range(4):
            self.assertGreater(cell_brightness(sheet, i, 360, 640, 3), 20, f"cell {i}")

    def test_long_video_budget(self):
        self.assertEqual(self.m.frame_budget(600), 24)
        self.assertEqual(self.m.frame_budget(170), 12)
        self.assertEqual(self.m.frame_budget(3), 1)
        self.assertEqual(self.m.frame_budget(600, override=40), 40)


def _label_entry(url, caption="", ts=1720000000, name=None):
    values = [{"label": "URL", "value": url, "href": url}]
    if caption:
        values.append({"label": "Caption", "value": caption})
    values.append({"dict": [{"dict": [{"label": "Name", "value": "creator_handle",
                                       "href": "https://www.instagram.com/creator_handle"}],
                             "title": "Owner"}], "title": ""})
    entry = {"timestamp": ts, "label_values": values}
    if name:
        entry["label_values"].insert(0, {"label": "Name", "value": name})
    return entry


class Saved(unittest.TestCase):
    def setUp(self):
        self.tool = make_tool_copy()
        self.tmp = Path(tempfile.mkdtemp())

    def _export_zip(self) -> Path:
        mangled = "Here’s how \U0001F680".encode("utf-8").decode("latin-1")
        posts = [
            _label_entry("https://www.instagram.com/reel/AAA111/", mangled, 1720000000),
            _label_entry("https://www.instagram.com/p/BBB222/", "carousel tips", 1730000000),
        ]
        collections = [{
            "timestamp": 1730000500,
            "label_values": [
                {"label": "Name", "value": "Ad ideas"},
                {"dict": [{"dict": [
                    {"label": "URL", "value": "https://www.instagram.com/reel/AAA111/",
                     "href": "https://www.instagram.com/reel/AAA111/"},
                    {"dict": [{"label": "Name", "value": "#hashtag"}], "title": ""},
                ], "title": ""}], "title": "Saved posts"},
            ],
        }]
        path = self.tmp / "instagram-export.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("your_instagram_activity/saved/saved_posts.json", json.dumps(posts))
            z.writestr("your_instagram_activity/saved/saved_collections.json", json.dumps(collections))
            z.writestr("your_instagram_activity/likes/liked_posts.json",
                       json.dumps([_label_entry("https://www.instagram.com/p/LIKED9/")]))
        return path

    def test_import_new_format_zip(self):
        out = json.loads(run(self.tool, "scripts/saved.py", "import", str(self._export_zip())).stdout)
        self.assertEqual(out["new_in_queue"], 2)                    # liked post ignored, dupes merged
        self.assertEqual(out["collections"].get("Ad ideas"), 1)
        queue = json.loads((self.tool / "captures/_queue.json").read_text())
        first = next(i for i in queue["items"] if i["id"] == "ig-AAA111")
        self.assertEqual(first["caption"], "Here’s how \U0001F680")  # mojibake fixed
        self.assertEqual(first["collection"], "Ad ideas")
        self.assertEqual(first["saved_at"], "2024-07-03")

        nxt = json.loads(run(self.tool, "scripts/saved.py", "next", "--count", "1").stdout)
        self.assertEqual(nxt["next"][0]["id"], "ig-BBB222")         # newest save first
        run(self.tool, "scripts/saved.py", "mark", "ig-BBB222", "failed", "--reason", "gone")
        status = json.loads(run(self.tool, "scripts/saved.py", "status").stdout)
        self.assertEqual(status["by_status"], {"pending": 1, "failed": 1})
        run(self.tool, "scripts/saved.py", "retry-failed")
        status = json.loads(run(self.tool, "scripts/saved.py", "status").stdout)
        self.assertEqual(status["by_status"], {"pending": 2})

        again = json.loads(run(self.tool, "scripts/saved.py", "import", str(self._export_zip())).stdout)
        self.assertEqual(again["new_in_queue"], 0)                  # re-import is safe

    def test_import_old_format_and_links_file(self):
        old = {"saved_saved_media": [{"title": "someone", "string_map_data": {
            "Saved on": {"href": "https://www.instagram.com/p/OLD1/", "timestamp": 1600000000}}}]}
        old_collections = {"saved_saved_collections": [
            {"title": "Collection", "string_map_data": {"Name": {"value": "Recipes"},
                                                         "Creation Time": {"timestamp": 1600000000}}},
            {"title": "someone", "string_map_data": {"Name": {"href": "https://www.instagram.com/p/OLD1/",
                                                              "value": "someone"},
                                                     "Added Time": {"timestamp": 1600000001}}},
        ]}
        folder = self.tmp / "export" / "saved"
        folder.mkdir(parents=True)
        (folder / "saved_posts.json").write_text(json.dumps(old))
        (folder / "saved_collections.json").write_text(json.dumps(old_collections))
        out = json.loads(run(self.tool, "scripts/saved.py", "import", str(self.tmp / "export")).stdout)
        self.assertEqual(out["new_in_queue"], 1)
        self.assertEqual(out["collections"], {"Recipes": 1})

        links = self.tmp / "links.txt"
        links.write_text("check https://www.instagram.com/reel/NEW9/?igsh=x and "
                         "https://youtu.be/dQw4w9WgXcQ, also https://example.com/x\n")
        out = json.loads(run(self.tool, "scripts/saved.py", "import", str(links)).stdout)
        self.assertEqual(out["new_in_queue"], 2)


class CaptureFlow(unittest.TestCase):
    """The whole capture > save > search loop with downloads and Whisper stubbed out."""

    def setUp(self):
        self.tool = make_tool_copy()
        self.mods = load_modules(self.tool)
        self.tmp = Path(tempfile.mkdtemp())

    def test_reel_capture_save_and_search(self):
        video = make_video(self.tmp / "01.mp4", 720, 1280, 24, audio=True)
        fetch, transcribe, capture = self.mods["fetch"], self.mods["transcribe"], self.mods["capture"]

        def fake_instagram(url, work, config, caption_hint=""):
            media_dir = work / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(video, media_dir / "01.mp4")
            return {"source": "instagram", "url": "https://www.instagram.com/reel/XYZ/", "id": "ig-XYZ",
                    "title": "Three ways to batch content", "author": "creator", "caption": "Save this!",
                    "published": "2026-09-01", "duration_seconds": 0.0, "media": [media_dir / "01.mp4"],
                    "transcript": "", "transcript_source": "", "warnings": []}

        fetch.fetch_instagram = fake_instagram
        transcribe.whisper = lambda *a, **k: {
            "text": "First, batch your hooks. Then film them all in one go.", "language": "en",
            "segments": [{"start": 0.0, "end": 3.0, "text": "First, batch your hooks."}]}

        result = capture.capture("https://www.instagram.com/reel/XYZ/")
        self.assertEqual(result["transcript_source"], "whisper")
        self.assertEqual(len(result["frames"]), 5)
        self.assertEqual(len(result["sheets"]), 1)
        self.assertTrue((self.tool / result["sheets"][0]).exists())
        saved = json.loads((self.tool / "captures/_work/result.json").read_text())
        self.assertEqual(saved["transcript_segments"][0]["text"], "First, batch your hooks.")

        (self.tool / "captures/_work/summary.md").write_text(
            "A filming workflow: write hooks in a batch, then shoot them back to back.\n\n"
            "- On-screen: a checklist titled 'Batch day'\n")
        out = json.loads(run(self.tool, "scripts/notes.py", "save", "--about", "batching short-form video",
                             "--tags", "content, video", "--skill-worthy", "yes").stdout)
        note = (self.tool / "captures" / out["file"]).read_text()
        self.assertIn("## Transcript", note)
        self.assertIn("batch your hooks", note)
        self.assertIn("Save this!", note)
        index_md = (self.tool / "captures/index.md").read_text()
        self.assertIn("batching short-form video", index_md)
        self.assertIn("★", index_md)

        hits = json.loads(run(self.tool, "scripts/notes.py", "search", "batch", "hooks").stdout)
        self.assertEqual(hits["results"][0]["file"], out["file"])
        self.assertEqual(capture.already_captured("https://instagram.com/reels/XYZ/", {})["file"], out["file"])

    def test_queue_item_is_marked_done_on_save(self):
        links = self.tmp / "links.txt"
        links.write_text("https://www.instagram.com/reel/XYZ/\n")
        run(self.tool, "scripts/saved.py", "import", str(links))
        work = self.tool / "captures/_work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "result.json").write_text(json.dumps({
            "source": "instagram", "url": "https://www.instagram.com/reel/XYZ/", "id": "ig-XYZ",
            "title": "t", "transcript": "x", "frames": []}))
        (work / "summary.md").write_text("summary")
        out = json.loads(run(self.tool, "scripts/notes.py", "save").stdout)
        self.assertTrue(out["queue_marked_done"])
        status = json.loads(run(self.tool, "scripts/saved.py", "status").stdout)
        self.assertEqual(status["by_status"], {"done": 1})

    def test_local_silent_file_from_command_line(self):
        clip = make_video(self.tmp / "screen-recording.mp4", 1280, 720, 12, audio=False)
        out = run(self.tool, "scripts/capture.py", str(clip), "--budget", "150")
        result = json.loads(out.stdout)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["transcript_source"], "none (no speech)")
        self.assertEqual(len(result["frames"]), 3)
        self.assertEqual(result["source"], "file")
        # Big temporary files stay out of the tool folder.
        self.assertFalse(any(p.suffix in (".mp4", ".wav") for p in (self.tool / "captures").rglob("*")))

    def test_time_budget_stops_and_resumes(self):
        audio = self.tmp / "long-talk.wav"
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=300", str(audio))
        capture, transcribe = self.mods["capture"], self.mods["transcribe"]
        calls = []

        def slow_whisper(path, *a, **k):
            calls.append(path.name)
            time.sleep(1.0)
            return {"text": f"part{len(calls)}", "language": "en",
                    "segments": [{"start": 1.0, "end": 2.0, "text": f"part{len(calls)}"}]}

        transcribe.whisper = slow_whisper
        partials = 0
        for _ in range(10):
            try:
                result = capture.capture(str(audio), budget=1.5)
                break
            except capture.Partial as part:
                partials += 1
                self.assertRegex(part.progress, "transcribed|screenshots")
        else:
            self.fail("capture never finished")
        self.assertGreaterEqual(partials, 2)
        self.assertEqual(len(calls), 3)                     # 300s split into 120 + 120 + 60, none redone
        self.assertEqual(result["transcript"], "part1 part2 part3")
        saved = json.loads((self.tool / "captures/_work/result.json").read_text())
        for got, want in zip([s["start"] for s in saved["transcript_segments"]], [1.0, 121.0, 241.0]):
            self.assertAlmostEqual(got, want, delta=0.5)       # times line up across the parts

    def test_photo_post_without_downloader_falls_back_to_caption(self):
        fetch = self.mods["fetch"]
        fetch._wait_for_gap = lambda config: None
        fetch._ytdlp_info = lambda url, cookies=None: (None, "ERROR: There is no video in this post")
        fetch._gallery_dl = lambda url, folder, cookies: subprocess.CompletedProcess(
            [], 1, "", "No module named gallery_dl")
        result = fetch.fetch_instagram("https://www.instagram.com/p/PHOTO1/", self.tmp, {},
                                       caption_hint="5 tips for better sleep\n1. ...")
        self.assertEqual(result["title"], "5 tips for better sleep")
        self.assertTrue(result["warnings"])
        with self.assertRaises(RuntimeError):
            fetch.fetch_instagram("https://www.instagram.com/p/PHOTO2/", self.tmp, {})

    def test_error_messages_are_plain(self):
        explain = self.mods["fetch"].explain
        self.assertIn("network is blocked",
                      explain("Unable to connect to proxy Tunnel connection failed: 403 Forbidden", "u"))
        self.assertIn("private", explain("ERROR: This content is private", "u"))
        self.assertIn("login", explain("ERROR: [Instagram] abc: login required", "u"))


class Packaging(unittest.TestCase):
    """The folder opens straight in Claude Code: skill, settings and CLAUDE.md in place."""

    def test_skill_file(self):
        skill = (REAL_TOOL / ".claude/skills/video-to-skill/SKILL.md").read_text(encoding="utf-8")
        head = skill.split("---")[1]
        self.assertIn("name: video-to-skill", head)
        description = next(l for l in head.splitlines() if l.startswith("description:"))
        self.assertTrue(description.split(":", 1)[1].strip().startswith('"'), "description must be quoted")
        for script in ("setup.py", "capture.py", "notes.py", "saved.py"):
            self.assertIn(f"scripts/{script}", skill)

    def test_settings_allow_the_scripts(self):
        settings = json.loads((REAL_TOOL / ".claude/settings.json").read_text())
        allow = settings["permissions"]["allow"]
        for script in ("setup.py", "capture.py", "notes.py", "saved.py"):
            self.assertIn(f"Bash(python3 scripts/{script} *)", allow)
        self.assertIn("Edit(captures/**)", allow)

    def test_claude_md_points_at_the_skill(self):
        self.assertIn(".claude/skills/video-to-skill/SKILL.md", (REAL_TOOL / "CLAUDE.md").read_text())


class Setup(unittest.TestCase):
    def test_report_shape(self):
        tool = make_tool_copy()
        report = json.loads(run(tool, "scripts/setup.py").stdout)
        self.assertIn("ready", report)
        self.assertEqual({c["id"] for c in report["checks"]},
                         {"python", "ffmpeg", "yt-dlp", "whisper", "gallery-dl", "js-runtime"})
        out = run(tool, "scripts/setup.py", "--set", "whisper_model", "base")
        self.assertEqual(json.loads(out.stdout)["whisper_model"], "base")
        bad = run(tool, "scripts/setup.py", "--set", "nope", "1", check=False)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
