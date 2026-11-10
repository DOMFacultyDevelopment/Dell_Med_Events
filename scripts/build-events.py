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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------- Dell Med public events scraper ----------
# dellmed.utexas.edu/events (Craft CMS) renders events server-side.
# Links may be relative (/events/slug) or absolute (https://dellmed.utexas.edu/events/slug).
# Filter links use ?searchCriteria= and are excluded by requiring no "?" in the path.
# Month headers are <h2>Month YYYY</h2>. Month changes mid-section appear in text nodes.

def fetch_dellmed_public():
    import time as _time
    try:
        html = fetch(f"https://dellmed.utexas.edu/events?_={int(_time.time())}")
    except Exception as e:
        print(f"WARN: failed to fetch Dell Med public events: {e}", file=sys.stderr)
        return []

    html_clean = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html,
                        flags=re.S | re.I)

    # Diagnostic: confirm we got actual event content
    if "/events/" not in html_clean:
        print("WARN: Dell Med events page returned no event links — possible bot block", file=sys.stderr)
        return []

    events = []
    seen_urls = set()
    current_year = None
    current_month = None

    tokens = []
    for m in re.finditer(
        r"<h2[^>]*>(.*?)</h2>"
        # Match both relative (/events/slug) and absolute (https://dellmed.utexas.edu/events/slug)
        # Excludes filter links by requiring no "?" after the path
        r"|<a\s[^>]*href=\"((?:https://dellmed\.utexas\.edu)?/events/[^\"?#]+)\"[^>]*>(.*?)</a>"
        r"|([^<]{2,})",
        html_clean, re.S | re.I
    ):
        if m.group(1) is not None:
            tokens.append(("h2", re.sub(r"<[^>]+>", "", m.group(1)).strip()))
        elif m.group(2) is not None:
            title = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            if title:
                href = m.group(2)
                if not href.startswith("http"):
                    href = "https://dellmed.utexas.edu" + href
                tokens.append(("a_event", href, title))
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
            # Track mid-section month changes (e.g. "Nov 10" before an event, no h2)
            if current_year:
                mm = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d", tok[1], re.I)
                if mm:
                    mk = mm.group(1)[:3].capitalize()
                    if mk in MONTH_MAP:
                        current_month = MONTH_MAP[mk]
            context.append(tok[1])
            if len(context) > 20:
                context = context[-20:]

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

            # Date: look for "Oct 20", "Nov 3" etc. in context
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

            time_str = None
            tm = re.search(
                r"(\d{1,2}(?::\d{2})?\s*(?:[–\-]\s*\d{1,2}(?::\d{2})?)?\s*[ap]\.?m\.?)",
                ctx_text, re.I
            )
            if tm:
                time_str = tm.group(1).strip()

            location = None
            speaker = None
            after = []
            for j in range(i + 1, min(i + 15, len(tokens))):
                if tokens[j][0] == "text":
                    after.append(tokens[j][1])
                elif tokens[j][0] == "a_event":
                    break
            for ln in after[:5]:
                if not location and any(kw in ln for kw in [
                    "HDB", "HLB", "HTB", "Zoom", "Virtual", "Hybrid",
                    "Auditorium", "Building", "Center", "Hospital",
                    "DPRI", "Texas Union", "Dell Children", "UT Health", "AT&T"
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




# ---------- Rebuild index.html and ticker.html with embedded events ----------

def _events_js(events):
    """Return a JSON-safe JS array string of events for embedding in <script>."""
    import json as _json

    def clean(o):
        if isinstance(o, str):
            return o.replace("</script>", "<\\/script>")
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(i) for i in o]
        return o

    return _json.dumps(clean(events), ensure_ascii=False, separators=(",", ":"))


def build_index_html(events, updated):
    """
    Embeds events directly into index.html by replacing the
    'let EVENTS = [];' initializer so the page works in SharePoint
    iframes where fetch() may be blocked.
    """
    import re as _re
    index_path = ROOT / "index.html"
    if not index_path.exists():
        print("WARN: index.html not found, skipping embed", file=sys.stderr)
        return

    html = index_path.read_text(encoding="utf-8")
    data = _events_js(events)

    # Replace: let EVENTS = [...anything...];
    new_html = _re.sub(
        r"let EVENTS\s*=\s*\[.*?\]\s*;",
        f"let EVENTS = {data};",
        html,
        count=1,
        flags=_re.DOTALL,
    )

    if new_html == html:
        print("WARN: could not find 'let EVENTS = [];' in index.html", file=sys.stderr)
    else:
        index_path.write_text(new_html, encoding="utf-8")
        print(f"  Updated index.html with {len(events)} embedded events", file=sys.stderr)


def build_ticker_html(events, updated):
    """
    Embeds events directly into ticker.html by replacing the
    'let allEvents = [];' initializer so the ticker works in SharePoint
    iframes where fetch() may be blocked.
    """
    import re as _re
    ticker_path = ROOT / "ticker.html"
    if not ticker_path.exists():
        print("WARN: ticker.html not found, skipping embed", file=sys.stderr)
        return

    html = ticker_path.read_text(encoding="utf-8")
    data = _events_js(events)

    # Replace: let allEvents = [...anything...];
    new_html = _re.sub(
        r"let allEvents\s*=\s*\[.*?\]\s*;",
        f"let allEvents = {data};",
        html,
        count=1,
        flags=_re.DOTALL,
    )

    if new_html == html:
        print("WARN: could not find 'let allEvents = [];' in ticker.html", file=sys.stderr)
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

    # Embed events into index.html and ticker.html so both work in SharePoint iframes
    build_index_html(all_events, payload["updated"])
    build_ticker_html(all_events, payload["updated"])


if __name__ == "__main__":
    main()
