# video-to-skill (free version)

Turns YouTube videos, Instagram reels and posts, and video files into notes you can use later. When a video teaches a repeatable process, it can also turn that into a Claude skill. It can work through your whole Instagram Saved list.

It's free: no accounts, no API keys, no credits.

## Install: paste this into Claude Code

```
Install the video-to-skill tool from https://github.com/arpbusiness7-coder/video-to-skill. Download it into ~/Projects/video-to-skill, copy its skill file (.claude/skills/video-to-skill/SKILL.md) into ~/.claude/skills/video-to-skill/ so it works in every project, run its setup, then tell me in plain words what it does and how to use it.
```

Claude downloads it, adds the skill so it works in any project, installs everything it needs (no Homebrew, no Terminal, no admin password), then explains what it does and what to type. Prefer to do it by hand? See "Get started" below.

| Job | Tool | Cost |
|---|---|---|
| Download YouTube and Instagram videos | yt-dlp | free |
| Instagram photo posts and carousels | gallery-dl | free |
| Speech to text | Whisper, running on your computer | free |
| Screenshots and contact sheets | ffmpeg (a ready-made copy comes with setup) | free |
| Reading the screenshots and writing the summary | Claude | part of your Claude plan |

## Get started

Put the `video-to-skill` folder somewhere easy to find, like a `Projects` folder in your home folder. Then pick one of these:

### Option A: Claude Code (easiest, recommended)

1. Open the folder in Claude Code. Either:
   - in the Claude desktop app, go to the **Code** tab → **Project folder** → choose `video-to-skill`, or
   - in Terminal: `cd ~/Projects/video-to-skill` then `claude`
2. The first time, Claude Code asks whether you trust this folder. Say yes, which lets the tool run without asking at every step.
3. Type **set up**. Claude asks once, then installs everything the tool needs into one private folder: its own Python, ffmpeg and the rest. You don't need Homebrew, Terminal or your admin password. It's a one-time setup of a few minutes and about 1 GB.
4. Paste a link. That's it.

Nothing to configure: the skill, its instructions and its permissions are already in the folder (`.claude/` and `CLAUDE.md`).

### Option B: Claude desktop app chat

1. Let Claude reach the internet: click your name (bottom left) → Settings → Capabilities → Code execution and file creation → turn on Allow network egress → set allowed domains to All domains. (On a Team plan this is under Organization settings → Capabilities, and only the owner can change it.)
2. Start a new task and paste this:

> Set up video-to-skill for me. The folder is at ~/Projects/video-to-skill (change this to where you put it). Read .claude/skills/video-to-skill/SKILL.md in that folder and save it as a skill, then run its setup.

Claude asks to open the folder and shows you a card to save the skill (click Save), then installs the free tools into its own workspace. In this mode the tools may need re-downloading when you start a new task, which takes about a minute and happens automatically.

## Using it

Just ask Claude:

- "capture this https://www.instagram.com/reel/..."
- "what's useful in this video https://youtu.be/..."
- "go through my saved Instagram posts" (see below)
- "make a skill from my saved videos about cold email"

Tell it what you care about ("I run a cafe, only keep what I could use there") and it judges what's useful against that.

Notes are saved in the `captures` folder inside this one. `captures/index.md` lists everything, newest first, with skill-worthy ones starred. Claude only turns a video into a skill when you say yes.

## Going through your whole Instagram saved folder

1. **Get your saves from Instagram.** Apps can't read saved posts directly, so you request a copy: Instagram → Settings → Accounts Center → Your information and permissions → Export your information → Create export → Export to device. Then Customise information: pick **Saved**. Set Date range to **All time** and Format to **JSON**, then Start export.
2. **Wait for the email.** Instagram sends a download link, usually within a few hours. Download the .zip and leave it in Downloads.
3. **Tell Claude:** "Import my Instagram export from Downloads and go through my saves." It shows how many posts there are and which saved folders (collections) they're in.
4. **Pick how much to do,** e.g. "just the Marketing folder" or "the next 20". It's free, but each post uses some of your Claude usage.
5. **It works through them one at a time,** about a minute each, and pauses about 20 seconds between posts so Instagram doesn't block it. Deleted or private posts are skipped and listed.
6. **Stop whenever you like.** Say "carry on with my saves" later and it picks up where it left off. If you download a newer export later, only the new saves are added.

At the end you get a roll-up of themes, the most useful posts, and which ones could become skills.

## Doing the setup by hand (optional)

Claude does this for you. If you'd rather:

```
python3 scripts/setup.py            # what's missing
python3 scripts/setup.py --install  # everything, into ~/.video-to-skill-venv
python3 scripts/setup.py --prefetch # download the speech-to-text model now
```

`--install` uses uv (a free Python installer) to fetch its own current Python, then adds yt-dlp, gallery-dl, Whisper, a ready-made ffmpeg and Deno. It works on older Macs too, because it doesn't rely on the Python that comes with macOS or on Homebrew. Running it again later updates yt-dlp, which is the fix if YouTube or Instagram downloads start failing.

## Limits

- Public Instagram posts only. Private accounts don't work.
- YouTube, Instagram, and video, audio or image files on your computer. Not TikTok or LinkedIn yet.
- YouTube videos over 30 minutes that have captions are transcript-only unless you ask for screenshots.
- In the desktop app chat (Option B), the free tools may need re-downloading when you start a new task. Claude does this automatically, and it takes about a minute. In Claude Code they stay installed.

## If Instagram asks for a login

Public posts usually download without one. If you start seeing "login required" or "rate limited" errors:

1. Wait 15 to 30 minutes and try again. This is usually enough.
2. If it keeps happening, export a cookies file for instagram.com only. The "Get cookies.txt LOCALLY" browser extension does this. Save it in this folder as `instagram-cookies.txt`, then run `python3 scripts/setup.py --set cookies_file instagram-cookies.txt`. That file is your Instagram login, so keep it private and don't share this folder with it inside.

## Settings (config.json)

`python3 scripts/setup.py --set <key> <value>`

| Key | Default | What it does |
|---|---|---|
| captures_dir | captures | where notes go |
| whisper_model | small | tiny / base / small / medium / large-v3-turbo. Bigger is more accurate but slower. |
| language | (auto) | force a spoken language, e.g. `en` |
| cookies_file | (none) | see above |
| instagram_gap_seconds | 20 | minimum gap between Instagram downloads |
| models_dir | (default cache) | where the Whisper model is stored |

## Files

```
CLAUDE.md                              what Claude Code reads when you open this folder
.claude/skills/video-to-skill/SKILL.md the instructions Claude follows (the skill)
.claude/settings.json                  lets the scripts run without asking every time
scripts/setup.py                       check and install
scripts/capture.py                     capture one link or file
scripts/notes.py                       save, search and list notes
scripts/saved.py                       bulk mode (Instagram export or a list of links)
scripts/fetch.py                       downloading
scripts/media.py                       screenshots, contact sheets, audio
scripts/transcribe.py                  Whisper and caption clean-up
tests/                                 python3 -m unittest discover -s tests
```

The `.claude` folder is hidden in Finder. Press Cmd+Shift+. to show it.

## Sharing it with someone

Send them this folder **without** your `captures` folder (that's your notes), `config.json`, or any cookies file. Zip what's left, including the hidden `.claude` folder, and send it. The "Get started" steps above are all they need. If you put it on GitHub, the included `.gitignore` already leaves those personal files out.

---

Made by Big G
