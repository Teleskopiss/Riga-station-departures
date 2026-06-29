#!/usr/bin/env python3
"""
Minute scraper: fetches live GPS delays + dispatcher alerts.
Writes docs/live-delays.json.

Format - keyed by train number, only trains with delay > 0 included:
{
  "updated": "29.06.2026 20:01:00",
  "trains": {
    "6745": { "delay": 5 },
    "6747": { "delay": 2 }
  }
}
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
    """Build route_id -> train_nr lookup from the schedule file."""
    try:
        with open(os.path.abspath(SCHED_PATH), encoding="utf-8") as f:
            sched = json.load(f)
        return {
            tr["route_id"]: tr["nr"]
            for tr in sched.get("trains", [])
            if tr.get("route_id") and tr.get("nr")
        }
    except Exception as e:
        print(f"[delays] could not load schedule for route lookup: {e}")
        return {}


def fetch_gps_delays(route_to_nr: dict) -> dict:
    """
    Returns {train_nr: delay_minutes} for trains that are running late.

    The trainmap WebSocket sends each train's real-time data.
    We look at 'delayTime' (or 'delay') in milliseconds — positive means late.
    Some API versions use 'waitingTime' but that counts time until next stop,
    not delay. We try both fields and pick whichever is clearly a delay.
    """
    raw   = {}   # route_id -> delay_ms
    done  = threading.Event()
    msgs  = []   # collect raw entries for debug

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

                # Try explicit delay fields first
                delay_ms = rv.get("delayTime") or rv.get("delay") or 0

                # Fallback: some versions expose delay inside 'trainInfo'
                if delay_ms == 0:
                    info = rv.get("trainInfo") or {}
                    delay_ms = info.get("delayTime") or info.get("delay") or 0

                # Another fallback: 'lateTime' field
                if delay_ms == 0:
                    delay_ms = rv.get("lateTime") or 0

                raw[route_id] = int(delay_ms)
                msgs.append({"route_id": route_id, "delay_ms": int(delay_ms), "keys": list(rv.keys())})

            done.set()
            ws_app.close()
        except Exception as ex:
            print(f"[delays] ws parse error: {ex}")

    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_message=on_message,
        on_error=lambda *_: done.set(),
    )
    threading.Thread(target=ws_app.run_forever, daemon=True).start()
    done.wait(timeout=WS_TIMEOUT)
    ws_app.close()

    # Debug: print a sample of what keys the API returned
    if msgs:
        sample = msgs[0]
        print(f"[delays] ws sample keys: {sample['keys']}")
        delayed_samples = [m for m in msgs if m['delay_ms'] > 0][:5]
        if delayed_samples:
            print(f"[delays] delayed samples: {delayed_samples}")
        else:
            print(f"[delays] no positive delay_ms found in {len(msgs)} entries")

    result = {}
    for route_id, delay_ms in raw.items():
        delay_min = round(delay_ms / 60_000)
        if delay_min <= 0:
            continue
        nr = route_to_nr.get(route_id)
        if not nr:
            continue
        result[nr] = delay_min
        print(f"[delays] gps: train {nr} late {delay_min} min")
    return result


DELAY_RE = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kav\u0113jas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE,
)

def fetch_dispatcher_alerts() -> dict:
    """Returns {train_nr: delay_minutes} from dispatcher announcements on vivi.lv."""
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
        trains[nr] = {"delay": delay}  # dispatcher overrides gps

    output = {
        "updated": now_riga.strftime("%d.%m.%Y %H:%M:%S"),
        "trains":  trains,
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[delays] done -> {len(trains)} delayed trains written")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[delays] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
