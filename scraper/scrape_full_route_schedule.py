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

RIGA_TIME_ZONE = ZoneInfo("Europe/Riga")


def get_station_name(stop: dict) -> str:
    return str(
        stop.get("title")
        or stop.get("name")
        or stop.get("station")
        or ""
    ).strip()


def get_train_number(train: dict) -> str:
    return str(
        train.get("train")
        or train.get("nr")
        or train.get("trainNumber")
        or ""
    ).strip()


def format_time(value) -> str | None:
    """
    Convert a Vivi time value to HH:MM.

    Supports:
    - 2026-07-03T07:25:00Z
    - 2026-07-03 07:25:00
    - 07:25:00
    - 07:25
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # ISO datetime:
    # 2026-07-03T07:25:00Z
    # 2026-07-03 07:25:00
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


def get_arrival(stop: dict) -> str | None:
    return format_time(
        stop.get("arrival")
        or stop.get("arrive")
        or stop.get("arr")
    )


def get_departure(stop: dict) -> str | None:
    return format_time(
        stop.get("departure")
        or stop.get("depart")
        or stop.get("dep")
    )


def get_generic_time(stop: dict) -> str | None:
    return format_time(stop.get("time"))


def get_train_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]

        if isinstance(payload.get("trains"), list):
            return payload["trains"]

    raise ValueError(
        "trainGraph response does not contain a train list"
    )


def build_route(stops: list[dict], train_number: str) -> str:
    names = [
        get_station_name(stop)
        for stop in stops
        if get_station_name(stop)
    ]

    if not names:
        return train_number

    if names[0] == names[-1]:
        return names[0]

    return f"{names[0]} - {names[-1]}"


def build_schedule(stops: list[dict]) -> list[dict]:
    """
    Output rules:

    First station:
        departure only

    Intermediate stations:
        departure only

    Last station:
        arrival only

    Stops without any time:
        omitted

    Null values:
        never written
    """

    usable_stops = []

    for stop in stops:
        station = get_station_name(stop)

        if not station:
            continue

        arrival = get_arrival(stop)
        departure = get_departure(stop)
        generic_time = get_generic_time(stop)

        available_time = (
            departure
            or arrival
            or generic_time
        )

        if not available_time:
            continue

        usable_stops.append(
            {
                "station": station,
                "arrival": arrival,
                "departure": departure,
                "generic_time": generic_time,
                "available_time": available_time,
            }
        )

    if not usable_stops:
        return []

    result = []

    for index, stop in enumerate(usable_stops):
        is_first = index == 0
        is_last = index == len(usable_stops) - 1

        item = {
            "station": stop["station"],
        }

        if is_first:
            # First station is always departure.
            chosen_time = (
                stop["departure"]
                or stop["arrival"]
                or stop["generic_time"]
                or stop["available_time"]
            )

            if chosen_time:
                item["departure"] = chosen_time

        elif is_last:
            # Last station is always arrival.
            chosen_time = (
                stop["arrival"]
                or stop["departure"]
                or stop["generic_time"]
                or stop["available_time"]
            )

            if chosen_time:
                item["arrival"] = chosen_time

        else:
            # Every intermediate station is departure.
            chosen_time = (
                stop["departure"]
                or stop["arrival"]
                or stop["generic_time"]
                or stop["available_time"]
            )

            if chosen_time:
                item["departure"] = chosen_time

        if (
            "departure" in item
            or "arrival" in item
        ):
            result.append(item)

    return result


def sort_train_numbers(train_numbers: dict) -> OrderedDict:
    def sort_key(train_number: str):
        try:
            return (0, int(train_number))
        except ValueError:
            return (1, train_number)

    sorted_trains = OrderedDict()

    for train_number in sorted(
        train_numbers,
        key=sort_key,
    ):
        sorted_trains[train_number] = train_numbers[train_number]

    return sorted_trains


def scrape_schedule() -> dict:
    response = requests.get(
        TRAIN_GRAPH_URL,
        timeout=30,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Riga-Station-Full-Route-Schedule/1.0"
            ),
        },
    )

    response.raise_for_status()

    payload = response.json()
    trains = get_train_list(payload)

    print(
        f"Received {len(trains)} trains from trainGraph"
    )

    train_numbers = {}

    for train in trains:
        train_number = get_train_number(train)

        if not train_number:
            continue

        stops = train.get("stops") or []

        if not isinstance(stops, list):
            continue

        if not stops:
            continue

        schedule = build_schedule(stops)

        if not schedule:
            continue

        train_numbers[train_number] = {
            "route": build_route(
                stops,
                train_number,
            ),
            "schedule": schedule,
        }

    return {
        "updated": datetime.now(
            RIGA_TIME_ZONE
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "trainNumbers": sort_train_numbers(
            train_numbers
        ),
    }


def main() -> None:
    output = scrape_schedule()

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
            output,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("
")

    print(
        f"Wrote {len(output['trainNumbers'])} trains "
        f"to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()