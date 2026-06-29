#!/usr/bin/env python3
"""
Minute scraper: fetches live GPS delays + dispatcher alerts.
Writes two files:
  docs/live-delays.json        — departures from Rīga
  docs/live-arrival-delays.json — arrivals to Rīga

Format per file — keyed by train number:
{
  "updated": "30.06.2026 01:00:00",
  "trains": {
    "6745": {
      "gps_delay":        5,
      "dispatcher_delay": 0,
      "dispatcher_text":  ""
    }
  }
}

gps_delay        — minutes late from GPS/WebSocket (0 if no data)
dispatcher_delay — minutes extracted from dispatcher text on vivi.lv (0 if none)
dispatcher_text  — raw sentence from vivi.lv (empty string if none)

Delay is considered active as long as the text is present on the website.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import websocket

WS_URL            = "wss://trainmap.pv.lv/ws"
DEP_SCHED_PATH    = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-trains.json")
ARR_SCHED_PATH    = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-arrivals.json")
DEP_OUTPUT_PATH   = os.path.join(os.path.dirname(__file__), "..", "docs", "live-delays.json")
ARR_OUTPUT_PATH   = os.path.join(os.path.dirname(__file__), "..", "docs", "live-arrival-delays.json")
RIGA_TZ           = ZoneInfo("Europe/Riga")
WS_TIMEOUT        = 8

DISPATCHER_URLS = [
    "https://www.vivi.lv/en/",
    "https://www.vivi.lv/lv/",
]

# English: "Train No. 6747 Jelgava ... is delayed ~15 minutes"
RE_EN = re.compile(
    r"(Train\s+No[.\s]+(\d{3,5})[^.]*?is\s+delayed\s+~?\s*(\d+)\s*min[^.]*\.?)",
    re.IGNORECASE | re.DOTALL,
)
# Latvian: "vilciens 6747 kavējas 15 min"
RE_LV = re.compile(
    r"(vilcien[a-zāčēģīķļņšūž]*\s+(?:Nr\.?\s*)?(\d{3,5})[^.]*?kav[ēe][a-zāčēģīķļņšūž]*\s+~?\s*(\d+)\s*min[^.]*\.?)",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)


def load_route_map(path: str) -> dict:
    """Build route_id -> train_nr lookup from a schedule file."""
    try:
        with open(os.path.abspath(path), encoding="utf-8") as f:
            sched = json.load(f)
        return {
            tr["route_id"]: tr["nr"]
            for tr in sched.get("trains", [])
            if tr.get("route_id") and tr.get("nr")
        }
    except Exception as e:
        print(f"[delays] could not load route map from {path}: {e}")
        return {}


def load_train_nrs(path: str) -> set:
    """Return set of train numbers present in a schedule file."""
    try:
        with open(os.path.abspath(path), encoding="utf-8") as f:
            sched = json.load(f)
        return {str(tr["nr"]) for tr in sched.get("trains", []) if tr.get("nr")}
    except Exception as e:
        print(f"[delays] could not load train nrs from {path}: {e}")
        return set()


def fetch_gps_delays(route_to_nr: dict) -> dict:
    """
    Returns {train_nr: delay_minutes} from the trainmap WebSocket.
    """
    raw  = {}
    done = threading.Event()
    msgs = []

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
                delay_ms = rv.get("delayTime") or rv.get("delay") or 0
                if not delay_ms:
                    info     = rv.get("trainInfo") or {}
                    delay_ms = info.get("delayTime") or info.get("delay") or 0
                if not delay_ms:
                    delay_ms = rv.get("lateTime") or 0
                raw[route_id] = int(delay_ms)
                msgs.append({"route_id": route_id, "delay_ms": int(delay_ms)})
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


def fetch_dispatcher_alerts() -> dict:
    """
    Scrapes vivi.lv for dispatcher delay messages.
    Returns {train_nr: {"delay": int, "text": str}}.
    The delay is considered active as long as the text is present on the site.
    """
    alerts  = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; delay-scraper/1.0)"}

    for url in DISPATCHER_URLS:
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            resp.raise_for_status()
            text = resp.text

            for m in RE_EN.finditer(text):
                raw_sentence, nr, mins = m.group(1).strip(), m.group(2), int(m.group(3))
                alerts[nr] = {"delay": mins, "text": raw_sentence}
                print(f"[delays] dispatcher EN: train {nr} late {mins} min")

            for m in RE_LV.finditer(text):
                raw_sentence, nr, mins = m.group(1).strip(), m.group(2), int(m.group(3))
                alerts[nr] = {"delay": mins, "text": raw_sentence}
                print(f"[delays] dispatcher LV: train {nr} late {mins} min")

            if not RE_EN.search(text) and not RE_LV.search(text):
                print(f"[delays] no dispatcher delays found on {url}")

        except Exception as e:
            print(f"[delays] dispatcher fetch failed ({url}): {e}")

    return alerts


def build_output(gps: dict, dispatcher: dict, train_nrs: set) -> dict:
    """
    Merge GPS and dispatcher data for a set of train numbers.
    Every train with any delay is included; both tags always present.
    """
    all_nrs = (set(gps.keys()) | set(dispatcher.keys())) & train_nrs
    trains  = {}
    for nr in all_nrs:
        gps_min  = gps.get(nr, 0)
        disp     = dispatcher.get(nr, {})
        disp_min = disp.get("delay", 0)
        disp_txt = disp.get("text", "")
        if gps_min > 0 or disp_min > 0:
            trains[nr] = {
                "gps_delay":        gps_min,
                "dispatcher_delay": disp_min,
                "dispatcher_text":  disp_txt,
            }
    return trains


def main():
    now_riga = datetime.now(RIGA_TZ)
    print(f"[delays] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    # Build combined route map from both schedules
    dep_route_map = load_route_map(DEP_SCHED_PATH)
    arr_route_map = load_route_map(ARR_SCHED_PATH)
    combined_map  = {**dep_route_map, **arr_route_map}

    dep_nrs = load_train_nrs(DEP_SCHED_PATH)
    arr_nrs = load_train_nrs(ARR_SCHED_PATH)

    gps        = fetch_gps_delays(combined_map)
    dispatcher = fetch_dispatcher_alerts()
    print(f"[delays] {len(gps)} gps delayed, {len(dispatcher)} dispatcher alerts")

    dep_trains = build_output(gps, dispatcher, dep_nrs)
    arr_trains = build_output(gps, dispatcher, arr_nrs)

    timestamp = now_riga.strftime("%d.%m.%Y %H:%M:%S")

    for out_path, trains in [
        (DEP_OUTPUT_PATH, dep_trains),
        (ARR_OUTPUT_PATH, arr_trains),
    ]:
        output = {
            "updated": timestamp,
            "trains":  trains,
        }
        p = os.path.abspath(out_path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"[delays] written → {len(trains)} trains to {p}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[delays] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
