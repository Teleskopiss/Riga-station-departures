#!/usr/bin/env python3
"""
Riga station departures scraper.

Sources:
  1. trainmap.vivi.lv/api/trainGraph  — scheduled timetable
  2. wss://trainmap.pv.lv/ws          — live GPS / delay status
  3. www.vivi.lv/lv/                  — dispatcher manual alerts

Delay priority (highest wins):
  dispatcher_delay  — always shown, not toggleable
  gps_delay         — stored, toggleable on display side
  0                 — scheduled time, never shown earlier
"""

import json
import os
import re
import threading
from datetime import datetime, timezone, date

import requests
import websocket

from track_data import get_track, get_platform

TRAIN_GRAPH_URL    = "https://trainmap.vivi.lv/api/trainGraph"
WS_URL             = "wss://trainmap.pv.lv/ws"
VIVI_URL           = "https://www.vivi.lv/lv/"
OUTPUT_PATH        = os.path.join(os.path.dirname(__file__), "..", "docs", "departures.json")
DEPARTURE_STATION  = "R\u012bg\u0101"
MAX_DEPARTURES     = 12
WS_COLLECT_SECONDS = 8


# ---------------------------------------------------------------------------
# 1. Scheduled departures
# ---------------------------------------------------------------------------

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
            "nr":   str(t["train"]),
            "dest": stops[-1]["title"],
            "time": dep_dt.strftime("%H:%M"),
            "fuel": "E" if t.get("fuelType", "") == "\u0160" else "D",
            "gps_delay":        0,
            "dispatcher_delay": None,
            "delay_source":     "none",
            "_dep_dt":   dep_dt,
            "_route_id": str(t["id"]),
            "_train_nr": str(t["train"]),
        })

    departures.sort(key=lambda x: x["_dep_dt"])
    return departures[:MAX_DEPARTURES]


# ---------------------------------------------------------------------------
# 2. Live GPS status (WebSocket)
# ---------------------------------------------------------------------------

def fetch_live_status() -> dict[str, dict]:
    status: dict[str, dict] = {}
    done = threading.Event()

    def on_message(ws_app, message):
        try:
            msg = json.loads(message)
            if msg.get("type") != "back-end":
                return
            for entry in msg.get("data", []):
                rv       = entry.get("returnValue", {})
                route_id = str(rv.get("id", ""))
                if not route_id:
                    continue
                waiting_ms = rv.get("waitingTime", 0)
                extra_ms   = max(0, waiting_ms - 60_000)
                status[route_id] = {
                    "gps_delay_min": round(extra_ms / 60_000),
                    "stopped":       rv.get("stopped", False),
                    "gps_active":    rv.get("isGpsActive", False),
                    "finished":      rv.get("finished", False),
                }
            done.set()
            ws_app.close()
        except Exception:
            pass

    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_message=on_message,
        on_error=lambda *_: done.set(),
    )
    threading.Thread(target=ws_app.run_forever, daemon=True).start()
    done.wait(timeout=WS_COLLECT_SECONDS)
    ws_app.close()
    return status


# ---------------------------------------------------------------------------
# 3. Dispatcher alerts (vivi.lv)
# ---------------------------------------------------------------------------

DELAY_PATTERN = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kav\u0113jas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE,
)

def fetch_dispatcher_alerts() -> dict[str, int]:
    alerts: dict[str, int] = {}
    try:
        resp = requests.get(VIVI_URL, timeout=10,
                            headers={"Accept-Language": "lv"})
        resp.raise_for_status()
        for m in DELAY_PATTERN.finditer(resp.text):
            alerts[m.group(1)] = int(m.group(2))
            print(f"[dispatcher] Train {m.group(1)} late {m.group(2)} min")
    except Exception as e:
        print(f"[dispatcher] vivi.lv fetch failed: {e}")
    return alerts


# ---------------------------------------------------------------------------
# 4. Assign tracks & platforms
# ---------------------------------------------------------------------------

def assign_tracks(departures: list[dict]) -> list[dict]:
    """
    For each departure, determine track and platform.
    Tracks occupied by trains departing within 5 minutes are passed as
    'soon_occupied' to avoid placing two trains on the same track.
    """
    today = date.today()

    # Build set of tracks that will be occupied within 5 min windows
    # We iterate in time order (already sorted)
    result = []
    assigned_tracks: list[tuple[datetime, int]] = []  # (dep_dt, track)

    for d in departures:
        dep_dt = d["_dep_dt"]
        # tracks occupied by trains departing within 5 min before this one
        from datetime import timedelta
        window_start = dep_dt - timedelta(minutes=5)
        soon = {trk for (dt, trk) in assigned_tracks if dt >= window_start}

        track    = get_track(d["_train_nr"], d["dest"],
                             soon_occupied=soon, today=today)
        platform = get_platform(track)

        d["track"]    = track
        d["platform"] = platform
        assigned_tracks.append((dep_dt, track))
        result.append(d)

    return result


# ---------------------------------------------------------------------------
# 5. Merge delays
# ---------------------------------------------------------------------------

def merge_delays(departures: list[dict],
                 live:       dict[str, dict],
                 alerts:     dict[str, int]) -> list[dict]:
    clean = []
    for d in departures:
        route_id = d.pop("_route_id")
        train_nr = d.pop("_train_nr")
        d.pop("_dep_dt")

        ls        = live.get(route_id, {})
        gps_delay = ls.get("gps_delay_min", 0)

        if ls.get("stopped"):
            d["status"] = "stopped"
        elif ls.get("gps_active"):
            d["status"] = "gps"
        else:
            d["status"] = "scheduled"

        if train_nr in alerts:
            d["dispatcher_delay"] = alerts[train_nr]
            d["gps_delay"]        = gps_delay
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
    return clean


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now()
    print(f"[scraper] {now:%H:%M:%S}")

    departures = fetch_scheduled(now)
    print(f"[scraper] {len(departures)} upcoming departures")

    departures = assign_tracks(departures)

    live   = fetch_live_status()
    print(f"[scraper] live status: {len(live)} trains")

    alerts = fetch_dispatcher_alerts()
    print(f"[scraper] dispatcher alerts: {len(alerts)}")

    departures = merge_delays(departures, live, alerts)

    alert_parts = [f"Vilciens {nr} kav\u0113jas {mins} min"
                   for nr, mins in alerts.items()]

    output = {
        "updated":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station":    "R\u012bg\u0101",
        "departures": departures,
        "alert":      "  |  ".join(alert_parts),
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[scraper] written -> {out_path}")


if __name__ == "__main__":
    main()
