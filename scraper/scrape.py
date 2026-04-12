#!/usr/bin/env python3
"""
Riga station departures scraper.

Delay priority (highest wins):
  1. dispatcher_delay  — manually posted on vivi.lv, ALWAYS shown, not toggleable
  2. gps_delay         — live WebSocket data, stored but toggleable on display side
  3. 0                 — scheduled time, never shown as earlier than timetable
"""

import json
import os
import re
import threading
from datetime import datetime, timezone

import requests
import websocket

TRAIN_GRAPH_URL = "https://trainmap.vivi.lv/api/trainGraph"
WS_URL          = "wss://trainmap.pv.lv/ws"
VIVI_URL        = "https://www.vivi.lv/lv/"
OUTPUT_PATH     = os.path.join(os.path.dirname(__file__), "..", "docs", "departures.json")
DEPARTURE_STATION  = "R\u012bg\u0101"
MAX_DEPARTURES     = 12
WS_COLLECT_SECONDS = 8


# ── 1. Scheduled departures (REST) ────────────────────────────────────────────

def fetch_scheduled(now: datetime) -> list[dict]:
    resp = requests.get(TRAIN_GRAPH_URL, timeout=15)
    resp.raise_for_status()
    trains = resp.json()["data"]

    departures = []
    for t in trains:
        stops = t.get("stops", [])
        if not stops or stops[0]["title"] != DEPARTURE_STATION:
            continue
        dep_str = stops[0]["departure"]
        dep_dt  = datetime.strptime(dep_str, "%Y-%m-%d %H:%M:%S")
        if dep_dt < now:
            continue
        departures.append({
            "nr":            str(t["train"]),
            "dest":          stops[-1]["title"],
            "time":          dep_dt.strftime("%H:%M"),  # always scheduled, never earlier
            "track":         "-",
            "fuel":          "E" if t.get("fuelType", "") == "\u0160" else "D",
            # delay fields — filled in later
            "gps_delay":         0,      # from WebSocket; toggleable on display
            "dispatcher_delay":  None,   # from vivi.lv alert; always shown
            "delay_source":      "none", # "none" | "gps" | "dispatcher"
            # internals stripped before write
            "_dep_dt":   dep_dt,
            "_route_id": str(t["id"]),
            "_train_nr": str(t["train"]),
        })

    departures.sort(key=lambda x: x["_dep_dt"])
    return departures[:MAX_DEPARTURES]


# ── 2. Live GPS delay (WebSocket) ──────────────────────────────────────────────

def fetch_live_status() -> dict[str, dict]:
    """
    Returns dict keyed by route_id:
      { gps_delay_min, stopped, gps_active, finished }
    gps_delay_min is best-effort: we measure extra waiting time beyond 1 min.
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
                waiting_ms  = rv.get("waitingTime", 0)
                extra_ms    = max(0, waiting_ms - 60_000)
                delay_min   = round(extra_ms / 60_000)
                status[route_id] = {
                    "gps_delay_min": delay_min,
                    "stopped":       rv.get("stopped", False),
                    "gps_active":    rv.get("isGpsActive", False),
                    "finished":      rv.get("finished", False),
                }
            done.set()
            ws_app.close()
        except Exception:
            pass

    ws_app = websocket.WebSocketApp(WS_URL, on_message=on_message,
                                    on_error=lambda *_: done.set())
    threading.Thread(target=ws_app.run_forever, daemon=True).start()
    done.wait(timeout=WS_COLLECT_SECONDS)
    ws_app.close()
    return status


# ── 3. Dispatcher alerts (vivi.lv scrape) ─────────────────────────────────────

# Pattern examples:
#   "Vilciens 6245 Rīga–Aizkraukle kavējas 20 min satiksmes negadījuma dēļ"
#   "Vilciens 813 Rīga–Daugavpils kavējas 15 min"
DELAY_PATTERN = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kavējas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE
)

def fetch_dispatcher_alerts() -> dict[str, int]:
    """
    Scrapes vivi.lv main page for dispatcher delay notices.
    Returns dict: train_nr (str) -> delay_minutes (int)
    These are ALWAYS shown, not toggleable.
    """
    alerts: dict[str, int] = {}
    try:
        resp = requests.get(VIVI_URL, timeout=10,
                            headers={"Accept-Language": "lv"})
        resp.raise_for_status()
        for m in DELAY_PATTERN.finditer(resp.text):
            train_nr  = m.group(1)
            delay_min = int(m.group(2))
            alerts[train_nr] = delay_min
            print(f"[dispatcher] Train {train_nr} late {delay_min} min")
    except Exception as e:
        print(f"[dispatcher] Could not fetch vivi.lv: {e}")
    return alerts


# ── 4. Merge all sources ───────────────────────────────────────────────────────

def build_output(departures: list[dict],
                 live: dict[str, dict],
                 alerts: dict[str, int]) -> dict:
    clean = []
    for d in departures:
        route_id = d.pop("_route_id")
        train_nr = d.pop("_train_nr")
        d.pop("_dep_dt")

        ls = live.get(route_id, {})
        gps_delay = ls.get("gps_delay_min", 0)

        if ls.get("stopped"):
            d["status"] = "stopped"
        elif ls.get("gps_active"):
            d["status"] = "gps"
        else:
            d["status"] = "scheduled"

        # Dispatcher alert overrides everything — always shown
        if train_nr in alerts:
            d["dispatcher_delay"] = alerts[train_nr]
            d["gps_delay"]        = gps_delay   # still stored for reference
            d["delay_source"]     = "dispatcher"
        elif gps_delay > 0:
            d["gps_delay"]        = gps_delay
            d["dispatcher_delay"] = None
            d["delay_source"]     = "gps"
        else:
            d["gps_delay"]        = 0
            d["dispatcher_delay"] = None
            d["delay_source"]     = "none"

        clean.append(d)

    # Build global alert string from any dispatcher notices
    # (full text scraped separately if needed; for now reconstruct from alerts)
    alert_parts = [
        f"Vilciens {nr} kavējas {mins} min"
        for nr, mins in alerts.items()
    ]
    alert_str = "  |  ".join(alert_parts)

    return {
        "updated":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station":    "R\u012bg\u0101",
        "departures": clean,
        "alert":      alert_str,
    }


def main():
    now = datetime.now()
    print(f"[scraper] {now:%H:%M:%S} — fetching scheduled data")
    departures = fetch_scheduled(now)
    print(f"[scraper] {len(departures)} upcoming departures from R\u012bg\u0101")

    print("[scraper] Connecting to WebSocket...")
    live = fetch_live_status()
    print(f"[scraper] Live status: {len(live)} active trains")

    print("[scraper] Checking vivi.lv dispatcher alerts...")
    alerts = fetch_dispatcher_alerts()
    print(f"[scraper] Dispatcher alerts: {len(alerts)}")

    output = build_output(departures, live, alerts)

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[scraper] Written → {out_path}")
    print(f"[scraper] Alert: '{output['alert']}'")


if __name__ == "__main__":
    main()
