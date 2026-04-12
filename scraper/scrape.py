#!/usr/bin/env python3
"""
Riga station departures scraper.
API times are in UTC — converted to Europe/Riga for display.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo

import requests
import websocket

from track_data import get_track, get_platform

TRAIN_GRAPH_URL    = "https://trainmap.vivi.lv/api/trainGraph"
WS_URL             = "wss://trainmap.pv.lv/ws"
VIVI_URL           = "https://www.vivi.lv/lv/"
OUTPUT_PATH        = os.path.join(os.path.dirname(__file__), "..", "docs", "departures.json")
RIGA_TZ            = ZoneInfo("Europe/Riga")
RIGA_NAMES         = {"rīgā", "riga", "rīga"}
MAX_DEPARTURES     = 12
WS_COLLECT_SECONDS = 8


# ---------------------------------------------------------------------------
# 1. Scheduled departures
# ---------------------------------------------------------------------------

def fetch_scheduled(now_riga: datetime) -> list[dict]:
    resp = requests.get(TRAIN_GRAPH_URL, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    trains  = payload["data"] if isinstance(payload, dict) and "data" in payload else payload

    departures = []
    for t in trains:
        stops = t.get("stops", [])
        if not stops:
            continue

        first_stop_name = str(stops[0].get("title") or stops[0].get("name") or "").strip()
        if first_stop_name.lower() not in RIGA_NAMES:
            continue

        dep_raw = stops[0].get("departure") or stops[0].get("time") or ""
        try:
            # API returns naive UTC datetime strings
            dep_utc = datetime.strptime(dep_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                # Time-only string — attach today's UTC date
                t_only  = datetime.strptime(dep_raw, "%H:%M:%S").replace(tzinfo=timezone.utc)
                today   = datetime.now(timezone.utc).date()
                dep_utc = t_only.replace(year=today.year, month=today.month, day=today.day)
            except ValueError:
                print(f"[warn] Cannot parse time '{dep_raw}' for train {t.get('train')}")
                continue

        # Convert to Riga local time for display
        dep_riga = dep_utc.astimezone(RIGA_TZ)

        # Skip trains that already departed (compare in Riga time)
        if dep_riga < now_riga:
            continue

        last_stop = str(stops[-1].get("title") or stops[-1].get("name") or "?")
        fuel_raw  = str(t.get("fuelType") or t.get("type") or "")
        fuel      = "E" if fuel_raw in ("Š", "E", "electric") else "D"

        departures.append({
            "nr":               str(t.get("train") or t.get("number") or ""),
            "dest":             last_stop,
            "time":             dep_riga.strftime("%H:%M"),   # Riga local HH:MM
            "fuel":             fuel,
            "gps_delay":        0,
            "dispatcher_delay": None,
            "delay_source":     "none",
            "_dep_dt":          dep_riga,
            "_route_id":        str(t.get("id") or ""),
            "_train_nr":        str(t.get("train") or t.get("number") or ""),
        })

    departures.sort(key=lambda x: x["_dep_dt"])
    print(f"[scraper] {len(departures)} departures from Riga")
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
# 3. Dispatcher alerts
# ---------------------------------------------------------------------------

DELAY_PATTERN = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kav\u0113jas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE,
)

def fetch_dispatcher_alerts() -> dict[str, int]:
    alerts: dict[str, int] = {}
    try:
        resp = requests.get(VIVI_URL, timeout=10, headers={"Accept-Language": "lv"})
        resp.raise_for_status()
        for m in DELAY_PATTERN.finditer(resp.text):
            alerts[m.group(1)] = int(m.group(2))
            print(f"[dispatcher] Train {m.group(1)} late {m.group(2)} min")
    except Exception as e:
        print(f"[dispatcher] fetch failed: {e}")
    return alerts


# ---------------------------------------------------------------------------
# 4. Assign tracks & platforms
# ---------------------------------------------------------------------------

def assign_tracks(departures: list[dict]) -> list[dict]:
    today    = date.today()
    assigned = []
    result   = []
    for d in departures:
        dep_dt       = d["_dep_dt"]
        window_start = dep_dt - timedelta(minutes=5)
        soon         = {trk for (dt, trk) in assigned if dt >= window_start}
        track        = get_track(d["_train_nr"], d["dest"], soon_occupied=soon, today=today)
        platform     = get_platform(track)
        d["track"]   = track
        d["platform"]= platform
        assigned.append((dep_dt, track))
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# 5. Merge delays
# ---------------------------------------------------------------------------

def merge_delays(departures, live, alerts):
    clean = []
    for d in departures:
        route_id = d.pop("_route_id")
        train_nr = d.pop("_train_nr")
        d.pop("_dep_dt")
        ls        = live.get(route_id, {})
        gps_delay = ls.get("gps_delay_min", 0)
        if ls.get("stopped"):        d["status"] = "stopped"
        elif ls.get("gps_active"):   d["status"] = "gps"
        else:                         d["status"] = "scheduled"
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
    now_riga = datetime.now(RIGA_TZ)
    print(f"[scraper] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    departures = fetch_scheduled(now_riga)
    departures = assign_tracks(departures)
    live       = fetch_live_status()
    alerts     = fetch_dispatcher_alerts()
    departures = merge_delays(departures, live, alerts)

    alert_parts = [f"Vilciens {nr} kav\u0113jas {mins} min" for nr, mins in alerts.items()]

    output = {
        "updated":    now_riga.strftime("%d.%m.%Y %H:%M:%S"),
        "station":    "Rīgā",
        "departures": departures,
        "alert":      "  |  ".join(alert_parts),
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[scraper] done → {len(departures)} departures written")


if __name__ == "__main__":
    main()
