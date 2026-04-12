#!/usr/bin/env python3
"""
Riga station departures scraper.
Fetches scheduled data from trainmap.vivi.lv/api/trainGraph,
enriches with live status from wss://trainmap.pv.lv/ws,
and writes docs/departures.json for GitHub Pages.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone

import requests
import websocket

TRAIN_GRAPH_URL = "https://trainmap.vivi.lv/api/trainGraph"
WS_URL = "wss://trainmap.pv.lv/ws"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "departures.json")
DEPARTURE_STATION = "R\u012bg\u0101"
MAX_DEPARTURES = 12
WS_COLLECT_SECONDS = 8  # how long to listen to WebSocket


# ── 1. Fetch scheduled departures from REST API ──────────────────────────────

def fetch_scheduled(now: datetime) -> list[dict]:
    """Return upcoming departures from Riga, sorted by time."""
    resp = requests.get(TRAIN_GRAPH_URL, timeout=15)
    resp.raise_for_status()
    trains = resp.json()["data"]

    departures = []
    for t in trains:
        stops = t.get("stops", [])
        if not stops:
            continue
        first = stops[0]
        if first["title"] != DEPARTURE_STATION:
            continue
        dep_str = first["departure"]  # "2026-04-12 11:35:00"
        dep_dt = datetime.strptime(dep_str, "%Y-%m-%d %H:%M:%S")
        if dep_dt < now:
            continue
        departures.append({
            "nr":    str(t["train"]),
            "dest":  stops[-1]["title"],
            "time":  dep_dt.strftime("%H:%M"),
            "track": "-",
            "fuel":  "E" if t.get("fuelType", "") == "\u0160" else "D",
            "delay": 0,
            "_dep_dt": dep_dt,
            "_route_id": str(t["id"]),
        })

    departures.sort(key=lambda x: x["_dep_dt"])
    return departures[:MAX_DEPARTURES]


# ── 2. Collect live status from WebSocket ────────────────────────────────────

def fetch_live_status() -> dict[str, dict]:
    """
    Connect to WS, collect one back-end message, return a dict keyed by
    route id -> {delay_min, stopped, gps_active}.
    """
    status: dict[str, dict] = {}
    done = threading.Event()

    def on_message(ws_app, message):
        try:
            msg = json.loads(message)
            if msg.get("type") != "back-end":
                return
            for entry in msg.get("data", []):
                rv = entry.get("returnValue", {})
                route_id = str(rv.get("id", ""))
                if not route_id:
                    continue

                # Estimate delay: compare expected arrival at next stop vs scheduled
                next_time_ms = rv.get("nextTime", 0)  # ms to next stop
                waiting_ms   = rv.get("waitingTime", 0)
                arriving_ms  = rv.get("arrivingTime", 0)
                extra_ms     = max(0, waiting_ms - 60_000)  # >1min waiting = delay
                delay_min    = round(extra_ms / 60_000)

                status[route_id] = {
                    "delay_min":  delay_min,
                    "stopped":    rv.get("stopped", False),
                    "gps_active": rv.get("isGpsActive", False),
                    "finished":   rv.get("finished", False),
                }
            done.set()
            ws_app.close()
        except Exception:
            pass

    def on_error(ws_app, error):
        done.set()

    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_message=on_message,
        on_error=on_error,
    )
    t = threading.Thread(target=ws_app.run_forever, daemon=True)
    t.start()
    done.wait(timeout=WS_COLLECT_SECONDS)
    ws_app.close()
    return status


# ── 3. Merge and write JSON ───────────────────────────────────────────────────

def build_output(departures: list[dict], live: dict[str, dict]) -> dict:
    clean = []
    for d in departures:
        route_id = d.pop("_route_id", None)
        d.pop("_dep_dt", None)

        ls = live.get(route_id, {})
        d["delay"] = ls.get("delay_min", 0)
        if ls.get("stopped"):
            d["status"] = "stopped"
        elif ls.get("gps_active"):
            d["status"] = "gps"
        else:
            d["status"] = "scheduled"
        clean.append(d)

    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station": "R\u012bg\u0101",
        "departures": clean,
        "alert": "",
    }


def main():
    now = datetime.now()
    print(f"[scraper] Fetching scheduled data at {now:%H:%M:%S}")
    departures = fetch_scheduled(now)
    print(f"[scraper] Got {len(departures)} upcoming departures")

    print("[scraper] Connecting to WebSocket for live status...")
    live = fetch_live_status()
    print(f"[scraper] Live status for {len(live)} active trains")

    output = build_output(departures, live)

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[scraper] Written to {out_path}")


if __name__ == "__main__":
    main()
