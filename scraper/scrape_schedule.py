#!/usr/bin/env python3
"""
Hourly scraper: fetches full day schedule from Riga.
Writes docs/full-day-trains.json.
All departure times stored as UTC ISO-8601 strings.
ESP32 downloads this once per hour.
"""

import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo

import requests

from track_data import get_track, get_platform

TRAIN_GRAPH_URL = "https://trainmap.vivi.lv/api/trainGraph"
OUTPUT_PATH     = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-trains.json")
RIGA_TZ         = ZoneInfo("Europe/Riga")
RIGA_NAMES      = {"r\u012bg\u0101", "riga", "r\u012bga"}


def fetch_full_day() -> list[dict]:
    resp = requests.get(TRAIN_GRAPH_URL, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    trains  = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    print(f"[schedule] API returned {len(trains)} total trains")

    today_utc = datetime.now(timezone.utc).date()
    result    = []

    for t in trains:
        stops = t.get("stops", [])
        if not stops:
            continue
        first = str(stops[0].get("title") or stops[0].get("name") or "").strip()
        if first.lower() not in RIGA_NAMES:
            continue

        dep_raw = stops[0].get("departure") or stops[0].get("time") or ""
        try:
            dep_utc = datetime.strptime(dep_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                t_only  = datetime.strptime(dep_raw, "%H:%M:%S").replace(tzinfo=timezone.utc)
                dep_utc = t_only.replace(year=today_utc.year, month=today_utc.month, day=today_utc.day)
            except ValueError:
                print(f"[warn] Cannot parse '{dep_raw}' for train {t.get('train')}")
                continue

        fuel_raw = str(t.get("fuelType") or t.get("type") or "")
        fuel     = "E" if fuel_raw in ("\u0160", "E", "electric") else "D"
        last     = str(stops[-1].get("title") or stops[-1].get("name") or "?")
        route_id = str(t.get("id") or "")

        result.append({
            "nr":        str(t.get("train") or t.get("number") or ""),
            "dest":      last,
            "dep_utc":   dep_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fuel":      fuel,
            "route_id":  route_id,
            "_dep_utc":  dep_utc,
        })

    result.sort(key=lambda x: x["_dep_utc"])
    return result


def assign_tracks(trains: list[dict]) -> list[dict]:
    today    = date.today()
    assigned = []
    out      = []
    for d in trains:
        dep_dt       = d.pop("_dep_utc")
        window_start = dep_dt - timedelta(minutes=5)
        soon         = {trk for (dt, trk) in assigned if dt >= window_start}
        track        = get_track(d["nr"], d["dest"], soon_occupied=soon, today=today)
        platform     = get_platform(track)
        d["track"]   = track
        d["platform"]= platform
        assigned.append((dep_dt, track))
        out.append(d)
    return out


def main():
    now_riga = datetime.now(RIGA_TZ)
    now_utc  = datetime.now(timezone.utc)
    print(f"[schedule] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    trains = fetch_full_day()
    trains = assign_tracks(trains)

    output = {
        "station":     "R\u012bg\u0101",
        "date":        now_riga.strftime("%Y-%m-%d"),
        "updated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated":     now_riga.strftime("%d.%m.%Y %H:%M:%S"),
        "total":       len(trains),
        "trains":      trains,
    }

    out_path = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[schedule] done \u2192 {len(trains)} trains written to {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[schedule] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
