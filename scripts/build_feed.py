#!/usr/bin/env python3
"""Build a valid RSS 2.0 feed.xml from data/feed-data.json.

Zero third-party dependencies — runs on any GitHub Actions runner with Python 3.
Mirrors the events.json -> GitHub Action -> GitHub Pages pattern already in use.

Usage:
    python scripts/build_feed.py            # reads data/feed-data.json, writes feed.xml
    python scripts/build_feed.py --check    # validates only, writes nothing (non-zero exit on error)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "feed-data.json"
OUT_FILE = ROOT / "feed.xml"


def parse_date(value: str) -> datetime:
    """Accept YYYY-MM-DD or a full ISO-8601 timestamp; return an aware UTC datetime."""
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value!r} (use YYYY-MM-DD)")


def build(data: dict) -> str:
    feed = data.get("feed", {})
    posts = data.get("posts", [])

    title = feed.get("title", "AI News Posts Across Campus")
    link = feed.get("link", "")
    description = feed.get("description", "")
    language = feed.get("language", "en-us")
    self_url = feed.get("self_url", "")

    # Normalize + sort newest first; guard against bad dates with a clear error.
    for i, p in enumerate(posts):
        if not p.get("title") or not p.get("link") or not p.get("date"):
            raise ValueError(f"Post #{i + 1} is missing a required field (title, link, date).")
        p["_dt"] = parse_date(p["date"])
    posts.sort(key=lambda p: p["_dt"], reverse=True)

    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
        f"    <link>{escape(link)}</link>",
        f"    <description>{escape(description)}</description>",
        f"    <language>{escape(language)}</language>",
        f"    <lastBuildDate>{now}</lastBuildDate>",
        f"    <generator>Dell Med Faculty Development RSS builder</generator>",
    ]
    if self_url:
        lines.append(
            f'    <atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>'
        )

    for p in posts:
        pub = format_datetime(p["_dt"])
        desc = p.get("description", "")
        source = p.get("source", "")
        body = desc
        if source:
            body = f"[{source}] {desc}".strip()
        lines += [
            "    <item>",
            f"      <title>{escape(p['title'])}</title>",
            f"      <link>{escape(p['link'])}</link>",
            f'      <guid isPermaLink="true">{escape(p["link"])}</guid>',
            f"      <pubDate>{pub}</pubDate>",
        ]
        if body:
            lines.append(f"      <description>{escape(body)}</description>")
        lines.append("    </item>")

    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = ap.parse_args()

    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found.", file=sys.stderr)
        return 1
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        xml = build(data)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.check:
        print(f"OK: {len(data.get('posts', []))} post(s) validated.")
        return 0

    OUT_FILE.write_text(xml, encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({len(data.get('posts', []))} item(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
