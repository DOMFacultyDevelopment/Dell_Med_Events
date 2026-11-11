#!/usr/bin/env python3
"""
Build events.json and dell-med-events.ics from three sources:
  1. Dell Med public events (scraped from dellmed.utexas.edu/events)
  2. UT Texas Today calendar (Localist .ics feed)
  3. Manually maintained internal events (data/internal-events.json)
"""

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_JSON = DATA_DIR / "events.json"
OUT_ICS = ROOT / "dell-med-events.ics"
INTERNAL_JSON = DATA_DIR / "internal-events.json"
PUBLIC_JSON = DATA_DIR / "public-events.json"   # NEW: manually maintained fallback

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------- Dell Med public events ----------

def fetch_dellmed_public():
    import time as _time
    today = date.today().isoformat()
    events = []
    seen = set()

    # --- Strategy 1: Localist JSON API ---
    api_url = f"https://dellmed.utexas.edu/api/2/events?pp=100&order=time&days=90&_={int(_time.time())}"
    try:
        req = urllib.request.Request(api_url, headers={
            **BROWSER_HEADERS,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        raw_events = data.get("events", [])
        print(f"  Localist API returned {len(raw_events)} event objects", file=sys.stderr)

        for item in raw_events:
            ev = item.get("event", item)
            title = ev.get("title", "").strip()
            if not title:
                continue
            url = ev.get("url") or f"https://dellmed.utexas.edu/events/{ev.get('urlname','')}"
            start_raw = ev.get("event_instances", [{}])[0].get("event_instance", {}).get("start") \
                        or ev.get("first_date", "")
            if not start_raw:
                continue
            iso = start_raw[:10]
            if iso < today:
                continue
            if url in seen:
                continue
            seen.add(url)
            time_str = None
            start_dt = ev.get("event_instances", [{}])[0].get("event_instance", {}).get("start", "")
            if start_dt and "T" in start_dt:
                try:
                    dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                    time_str = dt.strftime("%-I:%M %p").lower()
                except Exception:
                    pass
            location = (ev.get("location_name") or "").strip() or None
            events.append({
                "source": "public",
                "title": title,
                "url": url,
                "start": iso,
                "time": time_str,
                "location": location,
                "speaker": None,
            })

    except Exception as e:
        print(f"  Localist API failed: {e}", file=sys.stderr)

    # --- Strategy 2: Scrape individual known event pages ---
    known_slugs = [
        "mammogram-screenings-110226",
        "peripan-echo-series-the-ace-study",
        "research-nuts-bolts-animal-care-and-use-program",
        "pediatric-grand-rounds-a-primer-on-occult-spinal-dysraphism",
        "health-social-work-grand-rounds-health-equity",
        "cpan-series-what-every-pcp-should-know-about-behavior-disorders-in-early-childhood",
        "research-nuts-bolts-unanticipated-problems-11-12",
        "research-nuts-bolts-clinical-trial-and-study-design",
        "cpan-echo-series-psychological-vs-neuropsychological-assessment",
        "htri-in-partnership-with-texas-biologics-open-house",
        "peripan-echo-series-supporting-women-in-recovery-during-the-holiday-season",
        "peds-cardio-ai-conference-2026",
    ]

    for slug in known_slugs:
        url = f"https://dellmed.utexas.edu/events/{slug}"
        if url in seen:
            continue
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  WARN: could not fetch {url}: {e}", file=sys.stderr)
            continue

        tm = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else slug.replace("-", " ").title()

        iso = None
        dm = re.search(
            r"(?:Date|When)[:\s]*(?:\w+,\s*)?([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
            html, re.I
        )
        if dm:
            mon_key = dm.group(1)[:3].capitalize()
            if mon_key in MONTH_MAP:
                iso = f"{dm.group(3)}-{MONTH_MAP[mon_key]:02d}-{int(dm.group(2)):02d}"

        if not iso or iso < today:
            continue

        time_str = None
        tm2 = re.search(
            r"(?:Time|When)[:\s]*(\d{1,2}(?::\d{2})?\s*[–\-]\s*\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)",
            html, re.I
        )
        if tm2:
            time_str = tm2.group(1).strip()

        location = None
        lm = re.search(r"(?:Location|Where)[:\s]*([^\n<]{5,80})", html, re.I)
        if lm:
            location = lm.group(1).strip()

        seen.add(url)
        events.append({
            "source": "public",
            "title": title,
            "url": url,
            "start": iso,
            "time": time_str,
            "location": location,
            "speaker": None,
        })

    # --- Strategy 3: Load from data/public-events.json (manually maintained fallback) ---
    # This file is the safety net: when both scraping strategies return nothing
    # (e.g. site blocks the GitHub Actions IP), events added here always appear.
    # Format mirrors internal-events.json — see that file for reference.
    scraped_count = len(events)
    fallback_events = load_public_fallback(seen, today)
    events.extend(fallback_events)
    if fallback_events:
        print(f"  Loaded {len(fallback_events)} events from public-events.json fallback", file=sys.stderr)

    events.sort(key=lambda e: e["start"])
    print(f"  Dell Med public scraper: {len(events)} events parsed "
          f"({scraped_count} scraped, {len(fallback_events)} from fallback)", file=sys.stderr)
    return events


def load_public_fallback(seen_urls, today):
    """Load manually maintained public events from data/public-events.json."""
    if not PUBLIC_JSON.exists():
        return []
    try:
        data = json.loads(PUBLIC_JSON.read_text())
    except Exception as e:
        print(f"  WARN: could not parse public-events.json: {e}", file=sys.stderr)
        return []
    out = []
    for e in data.get("events", []):
        if not e.get("start") or not e.get("title"):
            continue
        if e["start"] < today:
            continue
        url = e.get("url") or "https://dellmed.utexas.edu/events"
        if url in seen_urls:
            continue   # already got it from scraping
        seen_urls.add(url)
        out.append({
            "source": "public",
            "title": e["title"],
            "url": url,
            "start": e["start"],
            "time": e.get("time"),
            "location": e.get("location"),
            "speaker": e.get("speaker"),
        })
    return out


# ---------- UT Localist .ics parser ----------

def parse_ics(text):
    events = []
    lines = []
    for raw in text.splitlines():
        if raw.startswith(" ") or raw.startswith("\t"):
            if lines:
                lines[-1] += raw[1:]
        else:
            lines.append(raw)
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current and "DTSTART" in current:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";", 1)[0]
            current[key] = val
    return events


def ics_date_to_iso(s):
    s = s.strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def fetch_ut_calendar():
    try:
        text = fetch("https://calendar.utexas.edu/calendar/1.ics")
    except Exception as e:
        print(f"WARN: failed to fetch UT calendar: {e}", file=sys.stderr)
        return []
    raw = parse_ics(text)
    today = date.today().isoformat()
    out = []
    seen = set()
    for ev in raw:
        iso = ics_date_to_iso(ev.get("DTSTART", ""))
        if not iso or iso < today:
            continue
        title = ev.get("SUMMARY", "").strip()
        if not title:
            continue
        key = (title, iso)
        if key in seen:
            continue
        seen.add(key)
        url = ev.get("URL", "").strip() or "https://calendar.utexas.edu/?school=austin"
        location = ev.get("LOCATION", "").strip().replace("\\,", ",").replace("\\;", ";")
        out.append({
            "source": "ut",
            "title": title,
            "url": url,
            "start": iso,
            "location": location or None,
        })
    return out


# ---------- Internal events ----------

def load_internal():
    if not INTERNAL_JSON.exists():
        return []
    try:
        data = json.loads(INTERNAL_JSON.read_text())
    except Exception as e:
        print(f"WARN: could not parse internal-events.json: {e}", file=sys.stderr)
        return []
    out = []
    for e in data.get("events", []):
        if not e.get("start") or not e.get("title"):
            continue
        out.append({
            "source": "internal",
            "title": e["title"],
            "url": e.get("url") or "https://intranet.dellmed.utexas.edu/events",
            "start": e["start"],
            "time": e.get("time"),
            "location": e.get("location"),
            "speaker": e.get("speaker"),
            "end": e.get("end"),
        })
    return out


# ---------- ICS output ----------

def ics_escape(s):
    if not s:
        return ""
    return (s.replace("\\", "\\\\")
             .replace(",", "\\,")
             .replace(";", "\\;")
             .replace("\n", "\\n"))


def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Dell Med Events Ticker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Dell Med Events",
        "X-WR-CALDESC:Aggregated events from Dell Medical School and UT Austin",
    ]
    for i, e in enumerate(events):
        d = e["start"].replace("-", "")
        end_d = (e.get("end") or e["start"]).replace("-", "")
        try:
            from datetime import timedelta
            sd = datetime.strptime(end_d, "%Y%m%d")
            ed = (sd + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            ed = end_d
        uid = f"{i}-{d}-{e['source']}@dell-med-events"
        title = e["title"]
        if e.get("time"):
            title = f"{title} ({e['time']})"
        description_parts = []
        if e.get("speaker"):
            description_parts.append(f"Speaker: {e['speaker']}")
        if e.get("time"):
            description_parts.append(f"Time: {e['time']}")
        description_parts.append(f"Source: {e['source']}")
        description_parts.append(f"More info: {e['url']}")
        out.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{d}",
            f"DTEND;VALUE=DATE:{ed}",
            f"SUMMARY:{ics_escape(title)}",
            f"DESCRIPTION:{ics_escape(chr(10).join(description_parts))}",
        ])
        if e.get("location"):
            out.append(f"LOCATION:{ics_escape(e['location'])}")
        out.append(f"URL:{e['url']}")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


# ---------- Embed events into HTML files ----------

def _make_events_js(events):
    raw = json.dumps(events, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("</script>", "<\\/script>")


def build_index_html(events, updated):
    index_path = ROOT / "index.html"
    if not index_path.exists():
        print("WARN: index.html not found", file=sys.stderr)
        return
    html = index_path.read_text(encoding="utf-8")
    data = _make_events_js(events)
    new_html = re.sub(
        r"let EVENTS\s*=\s*\[.*?\]\s*;",
        f"let EVENTS = {data};",
        html, count=1, flags=re.DOTALL,
    )
    if new_html == html:
        print("WARN: could not find 'let EVENTS = [...]' in index.html to replace", file=sys.stderr)
    else:
        # Also ensure renderCalendar() fires immediately on load (before fetch completes)
        if "renderCalendar();\nfetch('data/events.json" not in new_html:
            new_html = new_html.replace(
                "fetch('data/events.json?ts=' + Date.now())",
                "renderCalendar();\nfetch('data/events.json?ts=' + Date.now())",
                1,
            )
        index_path.write_text(new_html, encoding="utf-8")
        print(f"  Updated index.html with {len(events)} embedded events", file=sys.stderr)


def build_ticker_html(events, updated):
    ticker_path = ROOT / "ticker.html"
    if not ticker_path.exists():
        print("WARN: ticker.html not found", file=sys.stderr)
        return
    html = ticker_path.read_text(encoding="utf-8")
    data = _make_events_js(events)
    new_html = re.sub(
        r"let allEvents\s*=\s*\[.*?\]\s*;",
        f"let allEvents = {data};",
        html, count=1, flags=re.DOTALL,
    )
    if new_html == html:
        print("WARN: could not find 'let allEvents = [...]' in ticker.html to replace", file=sys.stderr)
    else:
        ticker_path.write_text(new_html, encoding="utf-8")
        print(f"  Updated ticker.html with {len(events)} embedded events", file=sys.stderr)


# ---------- Main ----------

def main():
    public_events = fetch_dellmed_public()
    print(f"Dell Med public: {len(public_events)} events", file=sys.stderr)
    ut_events = fetch_ut_calendar()
    print(f"UT calendar:    {len(ut_events)} events", file=sys.stderr)
    internal_events = load_internal()
    print(f"Internal:       {len(internal_events)} events", file=sys.stderr)

    internal_events = [e for e in internal_events if not e["title"].startswith("Example:")]

    today = date.today().isoformat()
    all_events = [e for e in public_events + internal_events + ut_events
                  if e.get("start", "") >= today]
    all_events.sort(key=lambda e: (e["start"], e["title"]))

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "public": len([e for e in all_events if e["source"] == "public"]),
            "internal": len([e for e in all_events if e["source"] == "internal"]),
            "ut": len([e for e in all_events if e["source"] == "ut"]),
        },
        "events": all_events,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT_JSON} ({len(all_events)} total events)", file=sys.stderr)

    ics = build_ics(all_events)
    OUT_ICS.write_text(ics)
    print(f"Wrote {OUT_ICS}", file=sys.stderr)

    build_index_html(all_events, payload["updated"])
    build_ticker_html(all_events, payload["updated"])


if __name__ == "__main__":
    main()
