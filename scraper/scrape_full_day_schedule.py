#!/usr/bin/env python3
"""
Full-day schedule scraper.
Fetches trainGraph, groups trains by train number, and writes:
  docs/full-day-schedule.json

Output format:
{
  "updated": "2026-07-03 13:00:00",
  "trainNumbers": {
    "3321": {
      "route": "Daugavpils - Turmantas",
      "schedule": [
        {"station": "Daugavpils", "departure": "07:25"},
        {"station": "I.p. 3 km", "departure": "07:37"},
        {"station": "Grīva", "departure": "07:41"},
        {"station": "Turmantas", "arrival": "08:25"}
      ]
    }
  }
}
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

TRAIN_GRAPH_URL = "https://trainmap.vivi.lv/api/trainGraph"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "full-day-schedule.json")
RIGA_TZ = ZoneInfo("Europe/Riga")
RIGA_NAMES = {"rīgā", "riga", "rīga"}


def normalize_station_name(value: str) -> str:
    return str(value or "").strip()


def is_riga_station(station: str) -> bool:
    return normalize_station_name(station).lower() in RIGA_NAMES


def fmt_time(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw)
    if len(s) >= 16 and s[10] in {"T", " "}:
        return s[11:16]
    return s[:5] if len(s) >= 5 else None


def pick_riga_stop(stops: list[dict]) -> dict | None:
    for stop in stops:
        name = normalize_station_name(stop.get("title") or stop.get("name") or stop.get("station"))
        if is_riga_station(name):
            return stop
    return None


def route_label(stops: list[dict], train_nr: str) -> str:
    if not stops:
        return train_nr
    names = [normalize_station_name(s.get("title") or s.get("name") or s.get("station")) for s in stops]
    names = [n for n in names if n]
    if not names:
        return train_nr
    first = names[0]
    last = names[-1]
    return f"{first} - {last}" if first != last else first


def extract_schedule(stops: list[dict]) -> list[dict]:
    out: list[dict] = []
    total = len(stops)

    for i, stop in enumerate(stops):
        station = normalize_station_name(stop.get("title") or stop.get("name") or stop.get("station"))
        if not station:
            continue

        arr = fmt_time(stop.get("arrival") or stop.get("arrive") or stop.get("arr"))
        dep = fmt_time(stop.get("departure") or stop.get("depart") or stop.get("dep"))
        time_field = fmt_time(stop.get("time"))

        item = {"station": station}

        if i == 0:
            chosen = dep or arr or time_field
            if chosen:
                item["departure"] = chosen
        elif i == total - 1:
            chosen = arr or dep or time_field
            if chosen:
                item["arrival"] = chosen
        else:
            chosen = dep or arr or time_field
            if chosen:
                item["departure"] = chosen

        out.append(item)

    return out


def fetch_full_day() -> dict:
    resp = requests.get(TRAIN_GRAPH_URL, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    trains = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    print(f"[schedule] API returned {len(trains)} total trains")

    grouped: OrderedDict[str, dict] = OrderedDict()

    for train in trains:
        train_nr = str(train.get("train") or train.get("nr") or "").strip()
        if not train_nr:
            continue

        stops = train.get("stops") or []
        if not isinstance(stops, list) or not stops:
            continue

        if not pick_riga_stop(stops):
            continue

        route = route_label(stops, train_nr)
        schedule = extract_schedule(stops)
        if not schedule:
            continue

        grouped[train_nr] = {
            "route": route,
            "schedule": schedule,
        }

    return {
        "updated": datetime.now(RIGA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "trainNumbers": grouped,
    }


def main() -> None:
    now_riga = datetime.now(RIGA_TZ)
    print(f"[schedule] {now_riga:%Y-%m-%d %H:%M:%S %Z}")

    data = fetch_full_day()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[schedule] wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
