"""
Save, list and search captures.

    python3 scripts/notes.py save --about "one line on what it is about"
           [--summary-file captures/_work/summary.md] [--type reel]
           [--tags "a, b"] [--skill-worthy yes|no|maybe] [--title "better title"]
    python3 scripts/notes.py search <words...> [--limit 10]
    python3 scripts/notes.py list [--tag TAG] [--skill-worthy yes]
    python3 scripts/notes.py reindex

`save` takes the transcript and details from captures/_work/result.json (the
last capture) and your summary from a file, writes captures/<slug>.md, and
updates index.md / index.json. If the capture came from the bulk queue, the
queue item is marked done.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import TOOL_DIR, captures_dir, load_config, read_json, slugify, utf8_streams, work_dir, write_json  # noqa: E402


def _index_path(root: Path) -> Path:
    return root / "index.json"


def load_index(root: Path) -> dict:
    data = read_json(_index_path(root), {"items": []})
    data.setdefault("items", [])
    return data


def render_index_md(root: Path, index: dict) -> None:
    rows = sorted(index["items"], key=lambda e: (e.get("captured", ""), e.get("saved_order", 0)),
                  reverse=True)
    lines = [
        "# Captures",
        "",
        f"{len(rows)} saved. Newest first. Skill-worthy ones are marked with a star.",
        "",
        "| Date | Title | Source | About |",
        "|---|---|---|---|",
    ]
    for entry in rows:
        title = (entry.get("title") or "untitled").replace("|", "/")
        about = (entry.get("about") or "").replace("|", "/")
        star = " ★" if entry.get("skill_worthy") == "yes" else ""
        lines.append(f"| {entry.get('captured', '')} | [{title}]({entry.get('file', '')}){star} "
                     f"| {entry.get('source', '')} | {about} |")
    (root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _unique_file(root: Path, slug: str, item_id: str, index: dict) -> Path:
    for entry in index["items"]:
        if entry.get("id") == item_id and entry.get("file"):
            return root / entry["file"]           # re-capture overwrites its own note
    taken = {e.get("file") for e in index["items"]}
    name, n = f"{slug}.md", 2
    while name in taken or (root / name).exists():
        name, n = f"{slug}-{n}.md", n + 1
    return root / name


def _mark_queue_done(root: Path, item_id: str, file_name: str) -> bool:
    queue_file = root / "_queue.json"
    queue = read_json(queue_file, None)
    if not queue:
        return False
    for item in queue.get("items", []):
        if item.get("id") == item_id:
            item.update(status="done", file=file_name, reason="", updated=time.strftime("%Y-%m-%d %H:%M"))
            write_json(queue_file, queue)
            return True
    return False


def save(args) -> None:
    config = load_config()
    root = captures_dir(config)
    work = work_dir(config)
    result = read_json(work / "result.json", None)
    if not result:
        sys.exit("No capture to save. Run capture.py first.")

    summary_file = Path(args.summary_file) if args.summary_file else work / "summary.md"
    if not summary_file.is_absolute() and not summary_file.exists():
        summary_file = TOOL_DIR / summary_file
    summary = summary_file.read_text(encoding="utf-8").strip() if summary_file.is_file() else ""
    if not summary:
        sys.exit(f"Write the summary to {summary_file} first.")

    index = load_index(root)
    title = args.title or result.get("title") or "untitled"
    slug_base = title
    if result.get("source") == "instagram" and result.get("author"):
        slug_base = f"{result['author']} {title}"
    path = _unique_file(root, slugify(slug_base), result["id"], index)

    frames = len(result.get("frames") or [])
    interval = result.get("frame_interval_seconds")
    shots = f"{frames} screenshots" + (f", one every {interval}s" if interval else "")
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    head = [
        f"# {title}",
        "",
        f"Source: {result.get('url', '')}",
    ]
    if result.get("author"):
        head.append(f"Author: {'@' if result.get('source') == 'instagram' else ''}{result['author']}")
    head += [
        f"Captured: {time.strftime('%Y-%m-%d')}" + (f" | Published: {result['published']}" if result.get("published") else ""),
        f"Type: {args.type or result.get('source', '')}, {shots}, transcript from {result.get('transcript_source') or 'none'}",
    ]
    if tags:
        head.append(f"Tags: {', '.join(tags)}")
    if args.skill_worthy:
        head.append(f"Skill-worthy: {args.skill_worthy}")
    body = head + ["", "## Summary", "", summary]
    if result.get("caption"):
        body += ["", "## Caption", "", result["caption"].strip()]
    body += ["", "## Transcript", "", (result.get("transcript") or "(no speech)").strip()]
    if result.get("warnings"):
        body += ["", "## Notes on this capture", ""] + [f"- {w}" for w in result["warnings"]]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")

    about = args.about or summary.splitlines()[0][:160]
    entry = {
        "id": result["id"],
        "file": path.name,
        "title": title,
        "url": result.get("url", ""),
        "source": result.get("source", ""),
        "author": result.get("author", ""),
        "captured": time.strftime("%Y-%m-%d"),
        "about": about,
        "tags": tags,
        "skill_worthy": args.skill_worthy or "",
    }
    index["items"] = [e for e in index["items"] if e.get("id") != result["id"]] + [entry]
    write_json(_index_path(root), index)
    render_index_md(root, index)
    queued = _mark_queue_done(root, result["id"], path.name)
    print(json.dumps({"saved": str(path), "file": path.name, "queue_marked_done": queued,
                      "total_captures": len(index["items"])}, indent=2))


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def search(args) -> None:
    root = captures_dir(load_config())
    terms = [w for w in _words(" ".join(args.words)) if len(w) > 1]
    if not terms:
        sys.exit("Give some words to search for.")
    hits = []
    for entry in load_index(root)["items"]:
        head = " ".join([entry.get("title", ""), entry.get("about", ""), " ".join(entry.get("tags", [])),
                         entry.get("author", "")]).lower()
        try:
            body = (root / entry["file"]).read_text(encoding="utf-8").lower()
        except (OSError, KeyError):
            body = ""
        score = 0.0
        matched = 0
        for term in terms:
            in_head, in_body = head.count(term), body.count(term)
            if in_head or in_body:
                matched += 1
            score += 5 * in_head + min(in_body, 10)
        if matched:
            score *= matched / len(terms)   # reward notes that match more of the words
            hits.append((score, entry))
    hits.sort(key=lambda h: h[0], reverse=True)
    out = [{"file": e["file"], "title": e.get("title"), "about": e.get("about"),
            "tags": e.get("tags"), "skill_worthy": e.get("skill_worthy"), "score": round(s, 1)}
           for s, e in hits[:args.limit]]
    print(json.dumps({"query": " ".join(args.words), "results": out}, indent=2, ensure_ascii=False))


def list_items(args) -> None:
    root = captures_dir(load_config())
    items = load_index(root)["items"]
    if args.tag:
        items = [e for e in items if args.tag.lower() in [t.lower() for t in e.get("tags", [])]]
    if args.skill_worthy:
        items = [e for e in items if e.get("skill_worthy") == args.skill_worthy]
    items = sorted(items, key=lambda e: e.get("captured", ""), reverse=True)
    print(json.dumps([{k: e.get(k) for k in ("file", "title", "about", "tags", "skill_worthy", "captured")}
                      for e in items], indent=2, ensure_ascii=False))


def reindex(_args) -> None:
    root = captures_dir(load_config())
    index = load_index(root)
    index["items"] = [e for e in index["items"] if (root / e.get("file", "")).is_file()]
    write_json(_index_path(root), index)
    render_index_md(root, index)
    print(f"index rebuilt: {len(index['items'])} captures")


def main() -> None:
    utf8_streams()
    parser = argparse.ArgumentParser(description="Save, list and search captures.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("save")
    p.add_argument("--summary-file")
    p.add_argument("--about", help="one line on what the video is about")
    p.add_argument("--type", help="reel, screen recording, talking head, photo post, carousel...")
    p.add_argument("--tags", help="comma separated")
    p.add_argument("--skill-worthy", choices=["yes", "no", "maybe"])
    p.add_argument("--title")
    p.set_defaults(func=save)

    p = sub.add_parser("search")
    p.add_argument("words", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=search)

    p = sub.add_parser("list")
    p.add_argument("--tag")
    p.add_argument("--skill-worthy", choices=["yes", "no", "maybe"])
    p.set_defaults(func=list_items)

    p = sub.add_parser("reindex")
    p.set_defaults(func=reindex)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
