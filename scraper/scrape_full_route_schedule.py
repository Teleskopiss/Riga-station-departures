#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


TRAIN_GRAPH_URL = (
    "https://trainmap.vivi.lv/api/trainGraph"
)

OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "docs",
    "full-day-schedule.json",
)

TIME_ZONE = ZoneInfo("Europe/Riga")


def station_name(stop: dict) -> str:
    return str(
        stop.get("title")
        or stop.get("name")
        or stop.get("station")
        or ""
    ).strip()


def train_number(train: dict) -> str:
    return str(
        train.get("train")
        or train.get("nr")
        or train.get("trainNumber")
        or ""
    ).strip()


def format_time(value) -> str | None:
    """
    Convert a time value into HH:MM.

    Supported formats:

    2026-08-07T07:25:00Z
    2026-08-07 07:25:00
    07:25:00
    07:25
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # ISO datetime format
    if len(text) >= 16 and text[10] in {"T", " "}:
        return text[11:16]

    # HH:MM:SS
    if (
        len(text) >= 8
        and text[2] == ":"
        and text[5] == ":"
    ):
        return text[:5]

    # HH:MM
    if len(text) >= 5 and text[2] == ":":
        return text[:5]

    return None


def arrival_time(stop: dict) -> str | None:
    return format_time(
        stop.get("arrival")
        or stop.get("arrive")
        or stop.get("arr")
    )


def departure_time(stop: dict) -> str | None:
    return format_time(
        stop.get("departure")
        or stop.get("depart")
        or stop.get("dep")
    )


def generic_time(stop: dict) -> str | None:
    return format_time(stop.get("time"))


def get_trains(payload) -> list[dict]:
    """
    Support the possible trainGraph response formats:

    [
      {...},
      {...}
    ]

    or:

    {
      "data": [
        {...}
      ]
    }

    or:

    {
      "trains": [
        {...}
      ]
    }
    """

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]

        if isinstance(payload.get("trains"), list):
            return payload["trains"]

    raise ValueError(
        "Could not find train list in trainGraph response"
    )


def build_route(
    stops: list[dict],
    number: str,
) -> str:
    names = []

    for stop in stops:
        name = station_name(stop)

        if name:
            names.append(name)

    if not names:
        return number

    if names[0] == names[-1]:
        return names[0]

    return f"{names[0]} - {names[-1]}"


def build_schedule(stops: list[dict]) -> list[dict]:
    """
    Time rules:

    First station:
        departure only

    Intermediate stations:
        departure only

    Last station:
        arrival only

    If a station has no usable time:
        omit that station

    Never write null values.
    """

    usable_stops = []

    for stop in stops:
        name = station_name(stop)

        if not name:
            continue

        arrival = arrival_time(stop)
        departure = departure_time(stop)
        fallback = generic_time(stop)

        available = (
            departure
            or arrival
            or fallback
        )

        if not available:
            continue

        usable_stops.append(
            {
                "station": name,
                "arrival": arrival,
                "departure": departure,
                "fallback": fallback,
                "available": available,
            }
        )

    if not usable_stops:
        return []

    schedule = []

    for index, stop in enumerate(usable_stops):
        first = index == 0
        last = index == len(usable_stops) - 1

        item = {
            "station": stop["station"],
        }

        if first:
            # First station always uses departure.
            value = (
                stop["departure"]
                or stop["arrival"]
                or stop["fallback"]
                or stop["available"]
            )

            if value:
                item["departure"] = value

        elif last:
            # Last station always uses arrival.
            value = (
                stop["arrival"]
                or stop["departure"]
                or stop["fallback"]
                or stop["available"]
            )

            if value:
                item["arrival"] = value

        else:
            # Intermediate stations always use departure.
            value = (
                stop["departure"]
                or stop["arrival"]
                or stop["fallback"]
                or stop["available"]
            )

            if value:
                item["departure"] = value

        if (
            "departure" in item
            or "arrival" in item
        ):
            schedule.append(item)

    return schedule


def sort_trains(
    trains: dict,
) -> OrderedDict:
    def sort_key(number: str):
        try:
            return (0, int(number))
        except ValueError:
            return (1, number)

    sorted_trains = OrderedDict()

    for number in sorted(
        trains.keys(),
        key=sort_key,
    ):
        sorted_trains[number] = trains[number]

    return sorted_trains


def scrape_schedule() -> dict:
    response = requests.get(
        TRAIN_GRAPH_URL,
        timeout=30,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Riga-Full-Route-Schedule-Scraper/1.0"
            ),
        },
    )

    response.raise_for_status()

    payload = response.json()
    trains = get_trains(payload)

    print(
        f"Received {len(trains)} trains "
        "from trainGraph"
    )

    output_trains = {}

    for train in trains:
        number = train_number(train)

        if not number:
            continue

        stops = train.get("stops") or []

        if not isinstance(stops, list):
            continue

        if not stops:
            continue

        schedule = build_schedule(stops)

        if not schedule:
            continue

        output_trains[number] = {
            "route": build_route(
                stops,
                number,
            ),
            "schedule": schedule,
        }

    return {
        "updated": datetime.now(
            TIME_ZONE
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "trainNumbers": sort_trains(
            output_trains
        ),
    }


def write_output(data: dict) -> None:
    output_directory = os.path.dirname(
        OUTPUT_PATH
    )

    os.makedirs(
        output_directory,
        exist_ok=True,
    )

    with open(
        OUTPUT_PATH,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("
")


def main() -> None:
    data = scrape_schedule()

    write_output(data)

    train_count = len(
        data["trainNumbers"]
    )

    print(
        f"Wrote {train_count} trains "
        f"to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
