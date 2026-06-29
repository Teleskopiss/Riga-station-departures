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
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import websocket

WS_URL      = "wss://trainmap.pv.lv/ws"
SCHED_PATH  = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-trains.json")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "live-delays.json")
RIGA_TZ     = ZoneInfo("Europe/Riga")
WS_TIMEOUT  = 8

# Dispatcher pages to scrape — try both; EN has "Train No. XXXX is delayed ~N minutes"
# LV has "vilciens XXXX kavējas N min" style text when shown
DISPATCHER_URLS = [
    "https://www.vivi.lv/en/",
    "https://www.vivi.lv/lv/",
]

# English format:  "Train No. 6747 Jelgava ... is delayed ~15 minutes"
#                  "Train No. 6747 ... is delayed ~15 min"
# Also handles without ~: "is delayed 15 minutes"
RE_EN = re.compile(
    r"Train\s+No[.\s]+(\d{3,5})[^.]*?is\s+delayed\s+~?\s*(\d+)\s*min",
    re.IGNORECASE | re.DOTALL,
)

# Latvian format:  "vilciens 6747 kavējas 15 min"
#                  "vilciena 6747 kavēšanās ~15 min"
RE_LV = re.compile(
    r"vilcien[a-zāčēģīķļņšūž]*\s+(?:Nr\.?\s*)?(\d{3,5})[^.]*?kav[ēe][a-zāčēģīķļņšūž]*\s+~?\s*(\d+)\s*min",
    re.IGNORECASE | re.UNICODE | re.DOTALL,
)


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
    Returns {train_nr: delay_minutes} from the trainmap WebSocket.
    Positive delayTime (ms) = late.
    """
    raw  = {}   # route_id -> delay_ms
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

                # Try explicit delay fields in priority order
                delay_ms = rv.get("delayTime") or rv.get("delay") or 0

                # Fallback: nested trainInfo sub-object
                if not delay_ms:
                    info     = rv.get("trainInfo") or {}
                    delay_ms = info.get("delayTime") or info.get("delay") or 0

                # Fallback: lateTime field
                if not delay_ms:
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

    if msgs:
        sample = msgs[0]
        print(f"[delays] ws sample keys: {sample['keys']}")
        delayed = [m for m in msgs if m["delay_ms"] > 0][:5]
        if delayed:
            print(f"[delays] delayed samples: {delayed}")
        else:
            print(f"[delays] no positive delay_ms in {len(msgs)} ws entries")

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
    Scrapes vivi.lv EN and LV pages for live dispatcher delay messages.

    English format (active on /en/):
        Train No. 6747 Jelgava (20:05) - Riga is delayed ~12 minutes.
    Latvian format (active on /lv/):
        Vilciens 6747 kavējas 12 min.
    """
    alerts = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; delay-scraper/1.0)"}

    for url in DISPATCHER_URLS:
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            resp.raise_for_status()
            text = resp.text

            # Print a snippet around any "delayed" / "kavē" match for debugging
            for keyword in ("is delayed", "kavē"):
                pos = text.lower().find(keyword.lower())
                if pos != -1:
                    snippet = text[max(0, pos-60):pos+120].replace("\n", " ")
                    print(f"[delays] dispatcher snippet ({url}): ...{snippet}...")
                    break

            found = False
            for m in RE_EN.finditer(text):
                nr, mins = m.group(1), int(m.group(2))
                alerts[nr] = mins
                print(f"[delays] dispatcher EN: train {nr} late {mins} min")
                found = True
            for m in RE_LV.finditer(text):
                nr, mins = m.group(1), int(m.group(2))
                alerts[nr] = mins
                print(f"[delays] dispatcher LV: train {nr} late {mins} min")
                found = True

            if not found:
                print(f"[delays] no dispatcher delays found on {url}")

        except Exception as e:
            print(f"[delays] dispatcher fetch failed ({url}): {e}")

    return alerts


def main():
    now_riga = datetime.now(RIGA_TZ)
    print(f"[delays] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    route_to_nr = load_route_to_nr()
    gps         = fetch_gps_delays(route_to_nr)
    dispatcher  = fetch_dispatcher_alerts()
    print(f"[delays] {len(gps)} gps delayed, {len(dispatcher)} dispatcher alerts")

    # Merge: dispatcher wins when both sources agree on the same train
    trains = {}
    for nr, delay in gps.items():
        trains[nr] = {"delay": delay}
    for nr, delay in dispatcher.items():
        trains[nr] = {"delay": delay}

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
