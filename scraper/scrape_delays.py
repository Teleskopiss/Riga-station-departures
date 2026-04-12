#!/usr/bin/env python3
"""
Minute scraper: fetches live GPS delays + dispatcher alerts.
Writes docs/live-delays.json.
Keyed by route_id (matches route_id in full-day-trains.json).
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
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "live-delays.json")
RIGA_TZ     = ZoneInfo("Europe/Riga")
WS_TIMEOUT  = 8


def fetch_live_status() -> dict:
    status = {}
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
                extra_ms   = max(0, waiting_ms - 60_000)
                status[route_id] = {
                    "gps_delay_min": round(extra_ms / 60_000),
                    "stopped":       rv.get("stopped", False),
                    "gps_active":    rv.get("isGpsActive", False),
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
    done.wait(timeout=WS_TIMEOUT)
    ws_app.close()
    return status


DELAY_RE = re.compile(
    r"[Vv]ilcien[si]\s+(\d+)[^\d].*?kav\u0113jas\s+(\d+)\s*min",
    re.IGNORECASE | re.UNICODE,
)

def fetch_dispatcher_alerts() -> dict:
    alerts = {}
    try:
        resp = requests.get(VIVI_URL, timeout=10, headers={"Accept-Language": "lv"})
        resp.raise_for_status()
        for m in DELAY_RE.finditer(resp.text):
            alerts[m.group(1)] = int(m.group(2))
            print(f"[delays] dispatcher: train {m.group(1)} late {m.group(2)} min")
    except Exception as e:
        print(f"[delays] dispatcher fetch failed: {e}")
    return alerts


def main():
    now_riga = datetime.now(RIGA_TZ)
    now_utc  = datetime.now(timezone.utc)
    print(f"[delays] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    live   = fetch_live_status()
    alerts = fetch_dispatcher_alerts()
    print(f"[delays] {len(live)} GPS entries, {len(alerts)} dispatcher alerts")

    output = {
        "updated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated":     now_riga.strftime("%d.%m.%Y %H:%M:%S"),
        "delays":      live,
        "alerts":      alerts,
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[delays] done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[delays] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
