---
name: video-to-skill
description: "Use when the user shares a YouTube link, an Instagram reel or post link, a video file, or their Instagram saved-posts export, and wants it captured, summarised, or turned into a skill. Triggers on phrases like capture this, what's useful in this reel, grab the transcript, turn this video into a skill, go through my saved Instagram posts, import my saves, or make a skill from my saved videos about a topic. Free and local, using yt-dlp, gallery-dl and Whisper, with no API keys. Also use when asked to set up or check video-to-skill."
---

# video-to-skill

Turns videos and posts into notes you can use later, and into skills when they teach a repeatable process. It reads the screen as well as the audio, because tutorials often only show the tool name, command or prompt on screen, and speech-to-text mishears names.

It is free. Downloads use yt-dlp and gallery-dl, speech-to-text runs on the user's own computer with Whisper, and screenshots come from ffmpeg. There are no accounts or API keys. It is universal: judge what is useful against what the user says they care about, and otherwise judge it in general terms.

Sources: YouTube, public Instagram reels and posts (including photo posts and carousels), and video, audio or image files on the computer. For anything else (TikTok, LinkedIn and so on), say plainly that this skill doesn't cover it and stop.

## Find the tool folder

The code lives in a folder named `video-to-skill` that contains `scripts/capture.py`. Call that folder TOOL. To find it:

1. **Claude Code with this folder open** (the usual way): the current working folder contains `scripts/capture.py`, so it is TOOL. Run everything from there.
2. **Claude desktop app chat** (Cowork), where you have a shell on the user's computer with connected folders under `$HOME/mnt/`: run `ls -d $HOME/mnt/*/video-to-skill $HOME/mnt/*/*/video-to-skill 2>/dev/null`. If nothing is found, ask the user where they put the folder (often `~/Projects` or `~/Documents`) and request access to it.
3. Otherwise check `~/Projects/video-to-skill` and then `~/.claude/skills/video-to-skill`.

If it still isn't found, ask the user where the video-to-skill folder is. Don't rebuild it from memory.

## How to run commands

- **Claude Code:** run the scripts from TOOL with your Bash tool: `python3 scripts/...`. Commands are cut off after 10 minutes, so add `--budget 540` to `capture.py` and give the command a 600000 ms timeout. If the output says `"state": "partial"` (exit code 2), run the exact same command again. It carries on from where it stopped, so nothing is redone. Open screenshots directly with your Read tool. The folder's `.claude/settings.json` already allows these scripts and writing in `captures/`, so the user isn't asked to approve every step.
- **Claude desktop app chat (Cowork):** run everything with the shell on the user's computer (device bash): `cd "<TOOL>" && python3 scripts/...`. Each call is cut off after about 3 minutes, and background jobs don't survive between calls, so:
  - Always add `--budget 150` to `capture.py` and pass the longest allowed timeout. If the output says `"state": "partial"`, run the same command again until the state is `done`, and give the user a short progress line on long videos.
  - To look at a screenshot, copy it across with the file-staging tool, then open the staged copy. Output paths are relative to TOOL, and the staging tool accepts the device-bash spelling, for example `~/mnt/Projects/video-to-skill/captures/_work/sheets/sheet_1.jpg`. Stage all of a capture's sheets in one call.

Every script prints JSON to stdout and progress to stderr. Use `python` instead of `python3` on Windows.

## Step 0: setup check (every run, fast)

```
python3 scripts/setup.py
```

- **`ready` is true:** go to Step 1 and don't mention setup.
- **Otherwise:** say in one or two plain sentences what's missing, then offer to run `python3 scripts/setup.py --install` yourself (ask first). It fetches everything the tool needs into one private folder in the home folder (`~/.video-to-skill-venv`): its own up-to-date Python, yt-dlp, gallery-dl, Whisper, a ready-made ffmpeg, and Deno (which YouTube downloads need). It needs no Homebrew, no Terminal and no admin password, and nothing is installed system-wide. It takes a few minutes and about 1 GB.
  - Straight after, run `python3 scripts/setup.py --prefetch` to download the speech model (about 460 MB for "small"), so it doesn't eat into the first capture.
  - If `--install` or `--prefetch` gets cut off by a time limit, run it again. It carries on where it stopped.
  - If `python3` itself is missing on a Mac, macOS offers to install Apple's free "command line developer tools". Tell the user to click Install, then try again.
  - If an install fails with a network or 403 error, run `python3 scripts/setup.py --net`. If sites show as blocked, tell the user their Claude network setting is blocking them. The fix is in Settings > Capabilities (network access): allow all domains, then start a new task. Stop there.
  - Running `--install` again later also updates yt-dlp, which is the fix when YouTube or Instagram downloads start failing.
- In Claude Code, the install lives in the user's home folder and stays put, so setup is one-time.
- In the desktop app chat, the private environment may need reinstalling in a new task, because the computer-side workspace can reset. That's normal: run `--install` and `--prefetch` again. That workspace also has limited memory, so keep `whisper_model` at "small" or below there.

### After setup (and right after the skill is installed): tell the user what this does

The first time setup finishes, or when the user has just installed this skill, finish with a short welcome in plain words. Keep it to this shape, with no jargon and no command output:

> **video-to-skill is ready.** It turns YouTube videos, Instagram reels and posts, and video files into short notes: what was said, plus anything shown on screen. When a video teaches a process you could repeat, it can turn that into a Claude skill (it always asks first). It's free and runs on your computer.
>
> **Try it by typing:**
> - a YouTube or Instagram link, e.g. "capture this https://www.instagram.com/reel/..."
> - "go through my Instagram saves" (I'll show you how to get them from Instagram)
> - "what have I saved about <topic>?"
> - "make a skill from my saved videos about <topic>"
>
> Your notes go in `<the real captures folder path>`. Tip: tell me what you care about ("I run a cafe") and I'll judge what's useful for that.

If the user only asked to set up or check video-to-skill, stop there.

## Step 1: capture

```
python3 scripts/capture.py "<link or file path>"
```

Options: `--frames` (force screenshots on a long video), `--no-frames`, `--max-frames N`, `--force` (capture again), `--budget SECONDS` (stop cleanly and resume, see above), `--fresh` (start over instead of resuming).

- If it prints `already_captured`, tell the user where the note is (`captures/<file>`) and ask whether to capture it again.
- Output fields: `title`, `author`, `caption`, `duration_seconds`, `transcript`, `transcript_source` (captions, auto-captions, whisper, or none), `frames`, `frame_times`, `sheets`, `warnings`.
- YouTube captions are used when available (free and fast). Whisper covers everything else, on the user's machine. Long YouTube videos with captions (over 30 minutes) skip screenshots unless you pass `--frames`.
- Instagram downloads are automatically spaced at least 20 seconds apart, so bulk runs don't get rate limited.
- Errors are plain sentences. Pass on the real cause (private post, removed, needs login, network blocked) rather than "it didn't work".

## Step 2: look at the screenshots

Use only the files listed in the output. Older files can linger in that folder. `sheets` are contact sheets with up to 6 screenshots each, read left to right and top to bottom in `frame_times` order. Plain black slots after the last screenshot are just empty padding. Single frames are also in `frames` if you need to zoom in on small text.

First decide how much the screen matters:

| Video type | What to do |
|---|---|
| Reel, Short, or screen recording | Read every sheet. The tool names, commands, prompts and prices are usually on screen. |
| Photo post or carousel | Read every sheet. The slides are the content. |
| Talking head with clean captions | One sheet is usually enough. |
| No speech found | The sheets and caption are the whole story. Read them all. |

When combining the sources:

- Treat the screenshots as what was **shown**, the transcript as what was **said**, and the caption as what was **written**. The caption is often the most reliable spelling of names.
- On anything written down (names, commands, links, numbers), the screenshots win. Say so when you correct the transcript.
- Where the on-screen content is the payload (a prompt, a script, a template, a list), copy it out word for word. Don't paraphrase it.
- Say how complete the coverage was. On a 60-second reel the screenshots are close to complete. On a long video they're a sample, so offer `--max-frames` if it matters.

## Step 3: summarise

Keep it short: a few sentences or a short list. Cover what it teaches or shows, the key steps, anything worth a second look, and whether it is **skill-worthy** (a repeatable process someone would run again). Mark anything that came only from the screen as `On-screen: ...`. Don't paste the transcript back into the chat.

## Step 4: save

Write the summary to `captures/_work/summary.md` inside TOOL, then run:

```
python3 scripts/notes.py save --about "<one line on what it's about>" --type "<reel / screen recording / talking head / photo post / carousel>" --tags "<2-4 short tags>" --skill-worthy <yes|no|maybe>
```

This writes `captures/<slug>.md` with the summary, caption and full transcript, and adds a line to `captures/index.md`. The transcript is copied by the script, so you never retype it. If the item came from the bulk queue, it's marked done.

## Step 5: verdict, and build a skill only on a yes

Tell the user where the note was saved and give a one-line verdict.

- **Not skill-worthy:** stop.
- **Skill-worthy:** ask plainly whether they want it turned into a skill. Never build one without a clear yes.

If they say yes:

1. Ask at most three short questions: when it should trigger, what it should produce, and anything to leave out.
2. Draft the SKILL.md. Use `name` (lowercase with hyphens) and a quoted `description` that says what it does and when to use it. An unquoted description containing a colon followed by a space breaks the file. The body should be the process as numbered steps, with prompts and commands copied word for word, plus a `Source:` line naming the capture file and link.
3. Show the draft. Then save it the way this environment saves skills:
   - **Claude Code:** ask whether it should work everywhere (save to `~/.claude/skills/<name>/SKILL.md`, the default) or only in this folder (`.claude/skills/<name>/SKILL.md` here). Tell them it's available from their next session.
   - **Claude desktop app chat:** use the skill-saving tool (such as `propose_skills`) so they get a Save card.
   - If a `skill-creator` skill is available, you can use it to draft and test the skill.

## Bulk mode: Instagram saved posts

Use this when the user wants to go through what they've saved over time.

1. **Get the export.** Instagram has no way for apps to read Saved posts, so it comes from the official export. Give the user these steps: Instagram > Settings > Accounts Center > Your information and permissions > Export your information > Create export > Export to device > Customise information (pick "Saved") > Date range: All time > Format: JSON > Start export. Meta emails a download link, usually within hours, sometimes a day or two. Any text file or message containing links also works.
2. **Import it.** Run `python3 scripts/saved.py import "<path to the .zip, folder or file>"` (it's usually `~/Downloads/instagram-...zip`). In the desktop app chat, ask for access to the folder holding it first. Show the user the count and collections, and ask how many to do now and whether to focus on particular collections.
3. **Work the queue**, one item at a time:
   - `python3 scripts/saved.py next --count 1`
   - `python3 scripts/capture.py <id>` (an id like `ig-ABC123` stands in for the link and its saved caption)
   - Look at the sheets **only when they're likely to matter**: no speech, a photo post or carousel, or the transcript or caption points at the screen ("link in bio", "this prompt", "step 3", "here's the template"). Otherwise work from the transcript and caption. That keeps a big batch quick.
   - Write a short summary and run `notes.py save`. That marks the item done.
   - On an error, run `python3 scripts/saved.py mark <id> failed --reason "<cause>"` and move on. If three in a row fail with login or rate-limit errors, stop, and tell the user to wait 15 to 30 minutes or set a cookies file (see README).
   - Every 10 items, give a one-line progress update.
4. **At the end**, give a short roll-up: how many were done, the main themes, and the skill-worthy ones. Offer to turn any of them into skills, one at a time or combined.

`saved.py status` shows progress. `saved.py retry-failed` puts failures back in the queue. Re-importing a newer export only adds new saves.

## Questions about saved notes

For "what have I saved about X?" or "find that reel about Y", run `python3 scripts/notes.py search <words>` (try a synonym or two). Read the best matching files in `captures/` and answer in a few lines, naming the notes and their links. For "what's skill-worthy?", run `notes.py list --skill-worthy yes`.

## A skill from several captures

When the user asks for something like "make a skill from my saved videos about X":

1. Run `python3 scripts/notes.py search <words>` (and try a couple of synonyms), or `notes.py list --skill-worthy yes`.
2. Read the top matching capture files and keep the ones that are actually about X.
3. Merge them into one process. Where sources disagree, note it. Keep prompts and commands word for word, and list every source capture at the bottom.
4. Show the draft and save it as in Step 5, only after a yes.

## Rules

- Don't install anything, system-wide or otherwise, without asking first.
- Never build a skill without an explicit yes.
- Only text is kept in `captures/`. Downloaded videos and audio go in a temporary folder outside the tool (`~/.cache/video-to-skill`), and screenshots go in `captures/_work/`, which is overwritten on every run.
- This tool is free, but every item still uses the user's Claude usage. For large batches, agree on a number first.
