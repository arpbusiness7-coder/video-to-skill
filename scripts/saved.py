"""
Bulk mode: work through your Instagram saved posts (or any list of links).

Instagram has no way for apps to read your Saved posts, so the route is the
official data export:

  Instagram app > Settings > Accounts Center > Your information and permissions
  > Export your information > Create export > Export to device
  > Customise information: pick "Saved" > Date range: All time > Format: JSON
  > Start export

Meta emails a download link, usually within a few hours (sometimes a day or two).

    python3 scripts/saved.py import <export .zip, folder, .json, .html or a text file of links> [--dry-run]
    python3 scripts/saved.py status
    python3 scripts/saved.py next [--count 5] [--collection NAME] [--oldest]
    python3 scripts/saved.py mark <id> failed|skipped|pending [--reason TEXT]
    python3 scripts/saved.py retry-failed

Then capture an item by its id:  python3 scripts/capture.py <id>
Saving it with notes.py marks it done in the queue.

Export-format notes: Meta's JSON stores each entry as a list of
{label, value, href} fields, nests collections under {"dict": [...]}, and
writes UTF-8 text as if it were Latin-1. Older exports used a
"string_map_data" shape instead. Both are handled.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (INSTAGRAM_POST, YOUTUBE, captures_dir, instagram_permalink, item_id,  # noqa: E402
                    load_config, read_json, utf8_streams, write_json, youtube_id)

POST_URL = re.compile(r"^https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(?:p|reels?|tv)/[\w-]+", re.I)
ANY_LINK = re.compile(r"https?://[^\s\"'<>)\]]+")


def fix_text(text: str) -> str:
    """Undo Meta's mangling, where each UTF-8 byte was saved as its own Latin-1
    character (so an apostrophe shows as 'â\x80\x99'). Clean text is left alone."""
    if not text or not any(ch in text for ch in "ÂÃâð"):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _iso(stamp) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(stamp)))
    except (TypeError, ValueError, OverflowError):
        return ""


# --------------------------------------------------------------------------
# Reading the export
# --------------------------------------------------------------------------

def _labels(items: list) -> dict:
    out = {}
    for item in items:
        if isinstance(item, dict) and "label" in item:
            out[item["label"]] = (fix_text(item.get("value") or ""), item.get("href") or "")
    return out


def harvest_json(data) -> list[dict]:
    found: list[dict] = []

    def add(url, caption="", collection="", saved_at=""):
        if url and POST_URL.match(url.strip()):
            found.append({"url": url.strip(), "caption": caption, "collection": collection,
                          "saved_at": saved_at})

    def walk(node, collection: str):
        if isinstance(node, list):
            current = collection
            for item in node:
                # Older exports: a collection header entry comes before its posts.
                if isinstance(item, dict) and isinstance(item.get("string_map_data"), dict):
                    smd = item["string_map_data"]
                    name = smd.get("Name") or {}
                    if isinstance(name, dict) and name.get("value") and not name.get("href") \
                            and ("Creation Time" in smd or "Update Time" in smd):
                        current = fix_text(name["value"])
                        continue
                walk(item, current)
            return
        if not isinstance(node, dict):
            return

        smd = node.get("string_map_data")
        if isinstance(smd, dict):
            for field in smd.values():
                if isinstance(field, dict) and field.get("href"):
                    add(field["href"], "", collection, _iso(field.get("timestamp")))

        group = node.get("label_values")
        nested = node.get("dict")
        items = group if isinstance(group, list) else (nested if isinstance(nested, list) else None)
        if items is None:
            for key, value in node.items():
                if key != "string_map_data":
                    walk(value, collection)
            return

        labels = _labels(items)
        if isinstance(group, list):
            # Only a top-level entry names a collection; nested groups (hashtags,
            # creator blocks) also carry a "Name" and must not win.
            name = labels.get("Name", ("", ""))[0]
            if name and not POST_URL.match(labels.get("Name", ("", ""))[1] or ""):
                collection = name
        value, href = labels.get("URL", ("", ""))
        add(href or value, labels.get("Caption", ("", ""))[0], collection, _iso(node.get("timestamp")))
        for child in items:
            walk(child, collection)

    walk(data, "")

    # Safety net for layouts nobody has seen yet: any post link anywhere.
    seen = {f["url"] for f in found}

    def scan(node):
        if isinstance(node, str):
            if POST_URL.match(node.strip()) and node.strip() not in seen:
                found.append({"url": node.strip(), "caption": "", "collection": "", "saved_at": ""})
                seen.add(node.strip())
        elif isinstance(node, list):
            for item in node:
                scan(item)
        elif isinstance(node, dict):
            for item in node.values():
                scan(item)

    scan(data)
    return found


def harvest_text(text: str) -> list[dict]:
    out = []
    for link in ANY_LINK.findall(text):
        link = link.rstrip(".,;")
        if POST_URL.match(link) or YOUTUBE.match(link):
            out.append({"url": link, "caption": "", "collection": "", "saved_at": ""})
    return out


def _is_saved_file(name: str) -> bool:
    lower = name.lower().replace("\\", "/")
    base = lower.rsplit("/", 1)[-1]
    return (base.startswith("saved") or "/saved/" in lower) and base.endswith((".json", ".html"))


def read_source(path: Path) -> tuple[list[dict], list[str]]:
    """Returns (items, files read)."""
    files: list[tuple[str, str]] = []
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file() and _is_saved_file(str(p.relative_to(path))):
                files.append((str(p.relative_to(path)), p.read_text(encoding="utf-8", errors="replace")))
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if _is_saved_file(name):
                    with archive.open(name) as handle:
                        files.append((name, io.TextIOWrapper(handle, encoding="utf-8", errors="replace").read()))
    elif path.is_file():
        files.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    else:
        raise SystemExit(f"Not found: {path}")

    items: list[dict] = []
    for name, text in files:
        if name.lower().endswith(".json"):
            try:
                items += harvest_json(json.loads(text))
                continue
            except json.JSONDecodeError:
                pass
        items += harvest_text(text)
    return items, [name for name, _ in files]


# --------------------------------------------------------------------------
# Queue
# --------------------------------------------------------------------------

def queue_path() -> Path:
    return captures_dir(load_config()) / "_queue.json"


def load_queue() -> dict:
    data = read_json(queue_path(), {"items": []})
    data.setdefault("items", [])
    return data


def _normalise(entry: dict) -> dict | None:
    url = entry["url"]
    try:
        key = item_id(url)
    except RuntimeError:
        return None
    if INSTAGRAM_POST.match(url):
        url = instagram_permalink(url)[0]
    elif YOUTUBE.match(url) and youtube_id(url):
        url = f"https://www.youtube.com/watch?v={youtube_id(url)}"
    return {**entry, "id": key, "url": url}


def cmd_import(args) -> None:
    source = Path(args.source).expanduser()
    raw, files = read_source(source)
    if not files:
        raise SystemExit("No saved-posts files found. Point this at the export .zip or its unzipped "
                         "folder (it needs the 'saved' section), or at a text file of links.")
    queue = load_queue()
    known = {item["id"]: item for item in queue["items"]}
    captured = {e.get("id") for e in read_json(captures_dir(load_config()) / "index.json",
                                                {"items": []}).get("items", [])}
    added, merged = 0, 0
    order = len(queue["items"])
    for entry in raw:
        item = _normalise(entry)
        if not item:
            continue
        if item["id"] in known:
            old = known[item["id"]]
            # Fill in details a previous import lacked (e.g. a links file, then the export).
            for field in ("caption", "collection", "saved_at"):
                if item.get(field) and not old.get(field):
                    old[field] = item[field]
                    merged += 1
            continue
        order += 1
        item.update(status="done" if item["id"] in captured else "pending", reason="", file="",
                    order=order, source="instagram" if item["id"].startswith("ig-") else "youtube")
        known[item["id"]] = item
        queue["items"].append(item)
        added += 1

    summary = {
        "files_read": files,
        "links_found": len(raw),
        "new_in_queue": added,
        "details_filled_in": merged,
        "queue_total": len(queue["items"]),
        "collections": dict(Counter(i.get("collection") or "(none)" for i in queue["items"]).most_common()),
    }
    if args.dry_run:
        summary["dry_run"] = True
    else:
        write_json(queue_path(), queue)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def cmd_status(_args) -> None:
    items = load_queue()["items"]
    by_status = Counter(i.get("status") for i in items)
    failed = [{"id": i["id"], "reason": i.get("reason", "")} for i in items if i.get("status") == "failed"]
    print(json.dumps({
        "total": len(items),
        "by_status": dict(by_status),
        "pending_by_collection": dict(Counter(i.get("collection") or "(none)" for i in items
                                              if i.get("status") == "pending").most_common()),
        "recent_failures": failed[-5:],
    }, indent=2, ensure_ascii=False))


def cmd_next(args) -> None:
    items = [i for i in load_queue()["items"] if i.get("status") == "pending"]
    if args.collection:
        items = [i for i in items if (i.get("collection") or "").lower() == args.collection.lower()]
    # Newest saves first by default (they tend to matter most), oldest with --oldest.
    items.sort(key=lambda i: (i.get("saved_at") or "", -i.get("order", 0)), reverse=not args.oldest)
    out = []
    for item in items[:args.count]:
        caption = item.get("caption") or ""
        out.append({"id": item["id"], "url": item["url"], "collection": item.get("collection", ""),
                    "saved_at": item.get("saved_at", ""),
                    "caption_start": caption[:200] + ("..." if len(caption) > 200 else "")})
    print(json.dumps({"pending": len(items), "next": out}, indent=2, ensure_ascii=False))


def cmd_mark(args) -> None:
    queue = load_queue()
    for item in queue["items"]:
        if item["id"] == args.id:
            item.update(status=args.status, reason=args.reason or "", updated=time.strftime("%Y-%m-%d %H:%M"))
            write_json(queue_path(), queue)
            print(json.dumps({"id": args.id, "status": args.status}))
            return
    raise SystemExit(f"{args.id} is not in the queue")


def cmd_retry(_args) -> None:
    queue = load_queue()
    count = 0
    for item in queue["items"]:
        if item.get("status") == "failed":
            item.update(status="pending", reason="")
            count += 1
    write_json(queue_path(), queue)
    print(json.dumps({"moved_back_to_pending": count}))


def lookup(key: str) -> dict | None:
    for item in load_queue()["items"]:
        if item["id"] == key:
            return item
    return None


def main() -> None:
    utf8_streams()
    parser = argparse.ArgumentParser(description="Bulk mode for Instagram saved posts or a list of links.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import")
    p.add_argument("source")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_import)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("next")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--collection")
    p.add_argument("--oldest", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("mark")
    p.add_argument("id")
    p.add_argument("status", choices=["failed", "skipped", "pending", "done"])
    p.add_argument("--reason")
    p.set_defaults(func=cmd_mark)

    sub.add_parser("retry-failed").set_defaults(func=cmd_retry)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
