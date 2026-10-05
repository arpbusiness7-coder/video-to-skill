# video-to-skill

This folder is a free tool that turns YouTube videos, Instagram reels and posts, and video files into short notes. When a video teaches a repeatable process, it can also turn that into a Claude skill. It can work through a whole Instagram saved-posts export.

How to work in this folder:

- For any link, video file, "my saves" request, or "set up", follow the `video-to-skill` skill in `.claude/skills/video-to-skill/SKILL.md`. This folder is TOOL.
- If the user just says hi or asks what this is, run `python3 scripts/setup.py` quietly, then reply with:
  1. One line on what this does: it turns videos and Instagram saves into notes, and into skills when they teach a process.
  2. Three things they can type: a YouTube or Instagram link, "go through my Instagram saves", or "make a skill from my saved videos about ...".
  3. If setup isn't ready, a single line offering to set it up (free, a few minutes).
- The person using this may not be technical. Keep replies short and in plain words, and don't show raw JSON or long command output unless they ask.
- When setup finishes for the first time, end with the short welcome described in the skill ("After setup"), so the user knows what this does and what to type.
- Ask before installing anything, and never build a skill without a clear yes.
- Notes go in `captures/`. Don't edit files in `scripts/` unless the user asks you to change how the tool works.
