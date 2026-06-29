#!/usr/bin/env python3
"""
Hourly scraper: fetches full-day ARRIVALS at Rīga station.
Writes docs/full-day-arrivals.json.
All arrival times stored as UTC ISO-8601 strings.
"""

import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo

import requests

TRAIN_GRAPH_URL = "https://trainmap.vivi.lv/api/trainGraph"
OUTPUT_PATH     = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-arrivals.json")
RIGA_TZ         = ZoneInfo("Europe/Riga")
RIGA_NAMES      = {"rīgā", "riga", "rīga"}


def find_riga_stop(stops: list) -> dict | None:
    for stop in stops:
        name = str(stop.get("title") or stop.get("name") or "").strip()
        if name.lower() in RIGA_NAMES:
            return stop
    return None


def fetch_full_day_arrivals() -> list[dict]:
    resp = requests.get(TRAIN_GRAPH_URL, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    trains  = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    print(f"[arrivals-sched] API returned {len(trains)} total trains")

    today_riga = datetime.now(RIGA_TZ).date()
    result     = []

    for t in trains:
        stops = t.get("stops", [])
        if not stops:
            continue

        # Only trains that ARRIVE at Rīgā — Rīgā must be the LAST stop.
        last_name = str(stops[-1].get("title") or stops[-1].get("name") or "").strip()
        if last_name.lower() not in RIGA_NAMES:
            continue

        riga_stop = find_riga_stop(stops)
        if riga_stop is None:
            continue

        # Prefer arrival time; fall back to departure/time field
        arr_raw = (
            riga_stop.get("arrival")
            or riga_stop.get("departure")
            or riga_stop.get("time")
            or ""
        )

        try:
            arr_riga = datetime.strptime(arr_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=RIGA_TZ)
        except ValueError:
            try:
                t_only   = datetime.strptime(arr_raw, "%H:%M:%S").replace(tzinfo=RIGA_TZ)
                arr_riga = t_only.replace(year=today_riga.year, month=today_riga.month, day=today_riga.day)
            except ValueError:
                print(f"[warn] Cannot parse '{arr_raw}' for train {t.get('train')}")
                continue

        arr_utc  = arr_riga.astimezone(timezone.utc)
        # Origin = first stop
        origin   = str(stops[0].get("title") or stops[0].get("name") or "?")
        route_id = str(t.get("id") or "")

        result.append({
            "nr":       str(t.get("train") or t.get("number") or ""),
            "origin":   origin,
            "arr_utc":  arr_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "route_id": route_id,
            "platform": 0,
            "track":    0,
            "_arr_utc": arr_riga,
        })

    result.sort(key=lambda x: x["_arr_utc"])
    # Remove sort key before writing
    for r in result:
        del r["_arr_utc"]
    return result


def main():
    now_riga = datetime.now(RIGA_TZ)
    now_utc  = datetime.now(timezone.utc)
    print(f"[arrivals-sched] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    trains = fetch_full_day_arrivals()

    output = {
        "station":     "Rīga",
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
    print(f"[arrivals-sched] done → {len(trains)} arrivals written to {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[arrivals-sched] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
