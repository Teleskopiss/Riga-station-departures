#!/usr/bin/env python3
"""
Minute scraper: fetches live GPS delays + dispatcher alerts.
Writes docs/live-delays.json.

New format - keyed by train number (nr), only trains with delay > 0 included:
{
  "updated": "20.06.2026 14:01:00",
  "trains": {
    "804": { "delay": 5 },
    "812": { "delay": 2 }
  }
}
If a train has no delay, it is not present in the dict at all.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import websocket

WS_URL      = "wss://trainmap.pv.lv/ws"
VIVI_URL    = "https://www.vivi.lv/lv/"
SCHED_PATH  = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-trains.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "live-delays.json")
RIGA_TZ     = ZoneInfo("Europe/Riga")
WS_TIMEOUT  = 8


def load_route_to_nr() -> dict:
    """Build a route_id -> train_nr lookup from the schedule file."""
    try:
        with open(os.path.abspath(SCHED_PATH), encoding="utf-8") as f:
            sched = json.load(f)
        return {tr["route_id"]: tr["nr"] for tr in sched.get("trains", []) if tr.get("route_id") and tr.get("nr")}
    except Exception as e:
        print(f"[delays] could not load schedule for route lookup: {e}")
        return {}


def fetch_gps_delays(route_to_nr: dict) -> dict:
    """Returns {train_nr: delay_minutes} for trains with delay > 0."""
    raw    = {}
    done   = threading.Event()

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
                delay_min  = round(max(0, waiting_ms - 60_000) / 60_000)
                raw[route_id] = delay_min
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
    done.wait(timeout=WS_TIMEOUT)
    ws_app.close()

    result = {}
    for route_id, delay_min in raw.items():
        if delay_min <= 0:
            continue  # on time - skip entirely
        nr = route_to_nr.get(route_id)
        if not nr:
            continue  # unknown train number - skip
        result[nr] = delay_min
        print(f"[delays] gps: train {nr} late {delay_min} min")
    return result


DELAY_RE = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kav\u0113jas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE,
)

def fetch_dispatcher_alerts() -> dict:
    """Returns {train_nr: delay_minutes} from dispatcher announcements."""
    alerts = {}
    try:
        resp = requests.get(VIVI_URL, timeout=10, headers={"Accept-Language": "lv"})
        resp.raise_for_status()
        for m in DELAY_RE.finditer(resp.text):
            nr  = m.group(1)
            min = int(m.group(2))
            alerts[nr] = min
            print(f"[delays] dispatcher: train {nr} late {min} min")
    except Exception as e:
        print(f"[delays] dispatcher fetch failed: {e}")
    return alerts


def main():
    now_riga = datetime.now(RIGA_TZ)
    print(f"[delays] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    route_to_nr = load_route_to_nr()
    gps         = fetch_gps_delays(route_to_nr)
    dispatcher  = fetch_dispatcher_alerts()
    print(f"[delays] {len(gps)} gps delayed, {len(dispatcher)} dispatcher alerts")

    # Merge: dispatcher wins if both sources report the same train
    trains = {}
    for nr, delay in gps.items():
        trains[nr] = {"delay": delay}
    for nr, delay in dispatcher.items():
        trains[nr] = {"delay": delay}  # overwrite gps with dispatcher

    output = {
        "updated": now_riga.strftime("%d.%m.%Y %H:%M:%S"),
        "trains":  trains,
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[delays] done → {len(trains)} delayed trains written")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[delays] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
