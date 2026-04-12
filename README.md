# Riga Station Departures

Live departure board for Rīga Centrālā stacija, powered by [vivi.lv](https://www.vivi.lv).

## How it works

```
ESP32 → fetches → GitHub Pages (departures.json)
                        ↑
               GitHub Actions (every 2 min)
                        ↑
               Python scraper
               (trainGraph REST + WebSocket)
```

## Setup

1. **Fork / clone** this repo
2. Go to **Settings → Pages** → Source: `Deploy from branch` → Branch: `main` → Folder: `/docs`
3. Enable **Actions** (Settings → Actions → Allow all)
4. The scraper runs automatically every 2 minutes
5. Your live JSON: `https://<username>.github.io/Riga-station-departures/departures.json`

## Repo structure

```
.github/workflows/scrape.yml   ← GitHub Actions (runs every 2 min)
scraper/scrape.py              ← Python scraper (REST + WebSocket)
scraper/requirements.txt       ← Python deps
docs/departures.json           ← Live JSON (served via GitHub Pages)
docs/index.html                ← Browser preview of the board
```

## JSON format

```json
{
  "updated": "2026-04-12T11:02:00Z",
  "station": "Rīga",
  "departures": [
    { "nr": "6717", "dest": "Jelgava",    "time": "11:35", "track": "-", "fuel": "E", "delay": 0,  "status": "scheduled" },
    { "nr": "813",  "dest": "Daugavpils", "time": "11:42", "track": "-", "fuel": "D", "delay": 3,  "status": "gps" }
  ],
  "alert": ""
}
```

## ESP32

The ESP32 fetches `departures.json` every 2 minutes and renders the dot-matrix display.
Arduino code coming soon.
