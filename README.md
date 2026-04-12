# Riga Station Departures

Live departure board for Rīga Centrālā stacija, https://teleskopiss.github.io/Riga-station-departures/

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
