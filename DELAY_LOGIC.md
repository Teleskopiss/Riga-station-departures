# Delay Logic

This document describes how delays are handled in the scraper and how the
ESP32 display should interpret the `departures.json` fields.

---

## Sources (priority order, highest first)

| Priority | Source | Field | Display behaviour |
|---|---|---|---|
| 1 | **Dispatcher** (vivi.lv manual) | `dispatcher_delay` | **Always shown**, not toggleable |
| 2 | **GPS / WebSocket** (`wss://trainmap.pv.lv/ws`) | `gps_delay` | Stored, **toggleable** on display |
| 3 | **Scheduled timetable** (trainGraph REST) | `time` | Baseline — never shown earlier |

---

## JSON fields per departure

```json
{
  "nr":               "6717",
  "dest":             "Jelgava",
  "time":             "11:35",      // always scheduled, never earlier
  "track":            "-",
  "fuel":             "E",
  "status":           "gps",        // "scheduled" | "gps" | "stopped"
  "gps_delay":        3,            // minutes late per WebSocket; 0 if none
  "dispatcher_delay": null,         // minutes late per vivi.lv; null if none
  "delay_source":     "gps"         // "none" | "gps" | "dispatcher"
}
```

---

## Display rules for ESP32

```
if delay_source == "dispatcher":
    show time + dispatcher_delay  ← ALWAYS, no toggle
    ignore gps_delay for display
elif delay_source == "gps":
    if gps_show_toggle == ON:
        show time + gps_delay
    else:
        show scheduled time only
else:
    show scheduled time
```

### Toggle behaviour
- `dispatcher_delay` is **not toggleable** — it is a confirmed, human-verified delay
- `gps_delay` is **toggleable** — it is an estimate from GPS position and may be noisy
- The toggle state lives on the ESP32 (button or config), not in the JSON

---

## Global alert banner

The top-level `alert` string in `departures.json` is populated from dispatcher
notices found on vivi.lv. It should be displayed as a scrolling ticker on the
board regardless of toggle state, because it may contain text beyond just delay
minutes (e.g. reason, affected stations).

When `alert` is an empty string `""`, the banner is hidden.

---

## Scraper flow

```
1. trainGraph REST  → scheduled times (base)
2. WebSocket        → gps_delay per route_id
3. vivi.lv scrape   → dispatcher_delay per train_nr
4. Merge (dispatcher > gps > none)
5. Write docs/departures.json
```
