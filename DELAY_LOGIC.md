# Delay & Track Logic

## Delay priority

| Priority | Source | Field | Display |
|---|---|---|---|
| 1 | Dispatcher (vivi.lv manual) | `dispatcher_delay` | Always shown, not toggleable |
| 2 | GPS / WebSocket | `gps_delay` | Stored, toggleable on display |
| 3 | Scheduled timetable | `time` | Baseline — never earlier |

## JSON fields per departure

```json
{
  "nr":               "6717",
  "dest":             "Jelgava",
  "time":             "11:35",
  "track":            5,
  "platform":         4,
  "fuel":             "E",
  "status":           "scheduled",
  "gps_delay":        0,
  "dispatcher_delay": null,
  "delay_source":     "none"
}
```

## ESP32 display rules

```
if delay_source == "dispatcher"  -> show time + dispatcher_delay  (ALWAYS, no toggle)
elif delay_source == "gps"       -> if toggle ON: show time + gps_delay
                                    if toggle OFF: show scheduled time
else                             -> show scheduled time
```

## Track priority

1. **Construction override** — active only within a defined date range (`CONSTRUCTION_OVERRIDES` in `track_data.py`)
2. **Explicit TRACK_MAP** — per train number
3. **Pattern default** — by train number structure / destination

## Platform map

| Track | Platform |
|---|---|
| 11, 12 | 1 |
| 1, 10  | 2 |
| 3, 4   | 3 |
| 5      | 4 |

## Adding construction overrides

In `scraper/track_data.py`, add an entry to `CONSTRUCTION_OVERRIDES`:

```python
CONSTRUCTION_OVERRIDES = [
    {
        "date_from": date(2026, 5, 1),
        "date_to":   date(2026, 5, 31),
        "tracks": {
            "6502": 4,   # normally 3, moved during works
        }
    },
]
```
The override is automatically ignored outside the date range.
