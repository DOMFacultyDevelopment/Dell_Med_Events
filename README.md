# Dell Med Events

Auto-aggregating events feed for Dell Medical School. Two views, one shared data source.

## Live pages

| Page | URL | Purpose |
|---|---|---|
| **Calendar** (home) | https://michael-dean22.github.io/Dell_Med_Events/ | Browseable list grouped by month, with filters and subscribe URL |
| **Ticker** | https://michael-dean22.github.io/Dell_Med_Events/ticker.html | Continuous vertical scroll for kiosks/wall displays |
| **iCal feed** | https://michael-dean22.github.io/Dell_Med_Events/dell-med-events.ics | Subscribable .ics for Outlook/etc. |

## How it works

A GitHub Action runs twice daily, executes `scripts/build-events.py`, and commits updated `data/events.json` and `dell-med-events.ics` files back to the repo. The two static pages (`index.html` and `ticker.html`) each fetch `data/events.json` on load and render their own view.

### Sources

| Source | How it gets in | Auth |
|---|---|---|
| **Dell Med public** (dellmed.utexas.edu/events) | Scraped from public HTML | None |
| **UT Texas Today** (calendar.utexas.edu) | Localist `.ics` feed (`/calendar/1.ics`) | None |
| **Dell Med intranet** (intranet.dellmed.utexas.edu/events) | Manually maintained in `data/internal-events.json` | UT EID required to read the page; manual sync needed |

## Adding internal events

Edit `data/internal-events.json` and commit. The Action runs on every push, so changes appear within a minute or two.

Required: `title`, `start` (YYYY-MM-DD), `url`. Optional: `time`, `location`, `speaker`, `end`.

```json
{
  "events": [
    {
      "title": "Department of Medicine Faculty Meeting",
      "start": "2026-06-15",
      "time": "12:00 PM - 1:00 PM",
      "location": "HDB 1.208",
      "url": "https://intranet.dellmed.utexas.edu/events/some-event"
    }
  ]
}
```

## Subscribing on the Dell Med Events Calendar (intranet)

The intranet calendar requires a UT login, so this can't be automated. Two paths:

- **If the intranet calendar supports iCal subscriptions** (most modern systems do): paste `https://michael-dean22.github.io/Dell_Med_Events/dell-med-events.ics` into its subscribe field. It will auto-refresh on whatever schedule that calendar uses.
- **If only one-time imports are supported**: download the `.ics` periodically and re-upload.

## Manual refresh

In the Actions tab on GitHub, find "Update Events" → **Run workflow**.

## File layout

```
.
├── index.html                       # Calendar view (home page)
├── ticker.html                      # Vertical scrolling ticker
├── dell-med-events.ics              # Auto-generated subscribe feed
├── data/
│   ├── events.json                  # Auto-generated merged feed (powers both pages)
│   └── internal-events.json         # MANUALLY EDITED - intranet events
├── scripts/
│   └── build-events.py              # Scrapes sources, builds events.json + .ics
└── .github/workflows/
    └── update-events.yml            # Runs the build script on a schedule
```

## Local development

```bash
python3 scripts/build-events.py
python3 -m http.server 8000   # then open http://localhost:8000
```
