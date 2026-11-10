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
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_JSON = DATA_DIR / "events.json"
OUT_ICS = ROOT / "dell-med-events.ics"
INTERNAL_JSON = DATA_DIR / "internal-events.json"

UA = "Mozilla/5.0 (compatible; DellMedEventsBot/1.0; +https://github.com/michael-dean22/Dell_Med_Events)"

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------- Dell Med public events scraper ----------
# dellmed.utexas.edu/events renders events server-side.
# Event links use ABSOLUTE URLs: https://dellmed.utexas.edu/events/slug
# Filter links use: https://dellmed.utexas.edu/events?searchCriteria=...
# The regex matches only slug-based absolute event URLs (no ? in path).

def fetch_dellmed_public():
    try:
        html = fetch("https://dellmed.utexas.edu/events")
    except Exception as e:
        print(f"WARN: failed to fetch Dell Med public events: {e}", file=sys.stderr)
        return []

    html_clean = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                        flags=re.S | re.I)

    events = []
    seen_urls = set()
    current_year = None
    current_month = None

    # Match absolute event URLs only — excludes ?searchCriteria= filter links
    tokens = []
    for m in re.finditer(
        r"<h2[^>]*>(.*?)</h2>"
        r"|<a\s[^>]*href=\"(https://dellmed\.utexas\.edu/events/[^\"?]+)\"[^>]*>(.*?)</a>"
        r"|([^<]{2,})",
        html_clean, re.S | re.I
    ):
        if m.group(1) is not None:
            tokens.append(("h2", re.sub(r"<[^>]+>", "", m.group(1)).strip()))
        elif m.group(2) is not None:
            title = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            if title:
                tokens.append(("a_event", m.group(2), title))
        elif m.group(4) is not None:
            t = m.group(4).strip()
            if t:
                tokens.append(("text", t))

    context = []
    for i, tok in enumerate(tokens):
        if tok[0] == "h2":
            hm = re.match(r"([A-Za-z]+)\s+(\d{4})", tok[1])
            if hm:
                mon_key = hm.group(1)[:3].capitalize()
                if mon_key in MONTH_MAP:
                    current_month = MONTH_MAP[mon_key]
                    current_year = int(hm.group(2))
            context = []

        elif tok[0] == "text":
            context.append(tok[1])
            # Track month changes in text nodes (e.g. "Nov 10" before an event)
            if current_year:
                dm = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}", tok[1], re.I)
                if dm:
                    mon_key = dm.group(1)[:3].capitalize()
                    if mon_key in MONTH_MAP:
                        current_month = MONTH_MAP[mon_key]
            if len(context) > 30:
                context = context[-30:]

        elif tok[0] == "a_event":
            href, title = tok[1], tok[2]
            if not current_year or not current_month:
                context = []
                continue
            if href in seen_urls:
                context = []
                continue
            seen_urls.add(href)

            ctx_text = " ".join(context)

            # Date from context
            day = None
            dm = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})", ctx_text, re.I)
            if dm:
                mon_key = dm.group(1)[:3].capitalize()
                if mon_key in MONTH_MAP:
                    current_month = MONTH_MAP[mon_key]
                day = int(dm.group(2))
            if not day:
                nums = re.findall(r"\b(\d{1,2})\b", ctx_text)
                if nums:
                    day = int(nums[-1])
            if not day:
                context = []
                continue

            iso = f"{current_year:04d}-{current_month:02d}-{day:02d}"

            # Time
            time_str = None
            tm = re.search(
                r"(\d{1,2}(?::\d{2})?\s*(?:[–\-]\s*\d{1,2}(?::\d{2})?)?\s*[ap]\.?m\.?)",
                ctx_text, re.I
            )
            if tm:
                time_str = tm.group(1).strip()

            # Location and speaker from tokens after this link
            location = None
            speaker = None
            after = []
            for j in range(i + 1, min(i + 20, len(tokens))):
                if tokens[j][0] == "text":
                    after.append(tokens[j][1])
                elif tokens[j][0] == "a_event":
                    break
            for ln in after[:8]:
                if not location and any(kw in ln for kw in [
                    "HDB", "HLB", "HTB", "Zoom", "Virtual", "Hybrid",
                    "Auditorium", "Building", "Center", "Hospital",
                    "DPRI", "Texas Union", "Dell Children", "UT Health"
                ]):
                    location = ln
                if not speaker and any(kw in ln for kw in ["M.D.", "Ph.D.", "D.O.", "MPH", "M.Ed.", "MBA"]):
                    speaker = ln

            events.append({
                "source":   "public",
                "title":    title,
                "url":      href,
                "start":    iso,
                "time":     time_str,
                "location": location,
                "speaker":  speaker,
            })
            context = []

    print(f"  Dell Med public scraper: {len(events)} events parsed", file=sys.stderr)
    return events


# ---------- UT Localist .ics parser ----------

def parse_ics(text):
    """Minimal ICS parser. Returns list of {title, start, end, url, location}."""
    events = []
    # Unfold lines
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
    """ICS date can be YYYYMMDD or YYYYMMDDTHHMMSSZ."""
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
        # Drop obvious noise / sports unless someone wants them; we keep them but tag source
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


# ---------- Internal events (manual JSON) ----------

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
        # All-day events: DTEND should be the day after
        try:
            sd = datetime.strptime(end_d, "%Y%m%d")
            from datetime import timedelta
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




# ---------- Rebuild index.html with embedded events ----------

def build_index_html(events, updated):
    """
    Reads index.html from the repo root and replaces the embedded ALL=
    array with fresh event data. This makes the page work in SharePoint
    iframes and other contexts where fetch() is blocked or uses the wrong
    base URL.
    """
    import json as _json
    index_path = ROOT / "index.html"
    if not index_path.exists():
        print("WARN: index.html not found, skipping embed", file=sys.stderr)
        return

    html = index_path.read_text(encoding="utf-8")

    # Encode events safely for embedding in a <script> block
    def clean(o):
        if isinstance(o, str):
            return o.replace("</script>", "<\\/script>")
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(i) for i in o]
        return o

    events_js = _json.dumps(clean(events), ensure_ascii=False, separators=(",", ":"))

    # Replace the var ALL=...;  line (handles both empty [] and previously embedded data)
    import re as _re
    # Match var ALL= followed by any JSON array (including large multi-line ones)
    # Use a non-greedy match anchored at ;
    new_html = _re.sub(
        r"var ALL=\[.*?\](?:\s*;|(?=\s*/\*))",
        f"var ALL={events_js};",
        html,
        count=1,
        flags=_re.DOTALL,
    )

    if new_html == html:
        print("WARN: could not find var ALL= in index.html to replace", file=sys.stderr)
    else:
        index_path.write_text(new_html, encoding="utf-8")
        print(f"Updated index.html with {len(events)} embedded events", file=sys.stderr)

# ---------- Main ----------

def main():
    public_events = fetch_dellmed_public()
    print(f"Dell Med public: {len(public_events)} events", file=sys.stderr)
    ut_events = fetch_ut_calendar()
    print(f"UT calendar:    {len(ut_events)} events", file=sys.stderr)
    internal_events = load_internal()
    print(f"Internal:       {len(internal_events)} events", file=sys.stderr)

    # Filter out the placeholder example
    internal_events = [e for e in internal_events
                       if not e["title"].startswith("Example:")]

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
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT_JSON} ({len(all_events)} total events)", file=sys.stderr)

    ics = build_ics(all_events)
    OUT_ICS.write_text(ics)
    print(f"Wrote {OUT_ICS}", file=sys.stderr)

    # Embed events into index.html so it works in SharePoint iframes
    build_index_html(all_events, payload["updated"])


if __name__ == "__main__":
    main()
