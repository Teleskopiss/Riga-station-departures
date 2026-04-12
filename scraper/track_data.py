"""
Track and platform assignments for Rīga Centrālā stacija.

PRIORITY (highest first):
  1. CONSTRUCTION_OVERRIDES  — active only within a defined date range (fill in when needed)
  2. TRACK_MAP               — explicit per-train-number assignments
  3. default_track()         — fallback by train number pattern / destination

Platform map:
  Platform 1  — Tracks 11, 12
  Platform 2  — Tracks 10, 1
  Platform 3  — Tracks 3, 4
  Platform 4  — Track 5 only
"""

from __future__ import annotations
from datetime import date
import random

# ---------------------------------------------------------------------------
# Explicit track map  (train_nr string -> track int)
# ---------------------------------------------------------------------------

TRACK_MAP: dict[str, int] = {
    # Skulte line (6100s)
    "6102": 10, "6104": 10, "6106": 10, "6108": 1,
    "6110": 11, "6112": 11, "6114": 11, "6118": 11,
    "6122": 10, "6126": 10, "6130": 11, "6134": 10,
    "6138": 10, "6140": 1,  "6142": 10, "6144": 11,
    "6146": 10, "6148": 10, "6152": 11, "6154": 11,
    "6156": 10, "6158": 10, "6160": 10, "6162": 11,
    "6164": 11, "6166": 11, "6168": 1,  "6170": 10,

    # Ogre / Aizkraukle / East diesel
    "6202": 4,  "6204": 4,  "6206": 4,  "6208": 10,
    "6210": 4,  "802":  10, "6212": 4,  "6214": 4,
    "804":  1,  "6216": 4,  "806":  1,  "6218": 4,
    "6220": 4,  "6222": 4,  "702":  11, "6224": 4,
    "6226": 4,  "704":  11, "810":  1,  "6228": 4,
    "6230": 4,  "6232": 4,  "6234": 4,  "6236": 4,
    "824":  4,  "6236-824": 4,           "6238": 4,
    "6240": 4,  "6242": 4,  "706":  1,  "708":  1,
    "6244": 4,  "812":  11, "6246": 4,  "6248": 1,
    "6250": 4,  "814":  10, "6252": 4,  "6254": 4,
    "816":  11, "820":  11, "6256": 4,  "6258": 4,
    "6260": 4,  "6262": 4,  "822":  1,  "6264": 4,
    "6266": 4,  "6268": 4,  "6270": 4,

    # Valmiera / Sigulda / Cēsis / Valga / Tallinn
    "862":  11, "712":  12, "830":  12, "832":  12,
    "872":  12, "834":  11, "874":  1,  "836":  12,
    "838":  1,  "840":  11, "842":  12, "864":  12,
    "844":  12, "876":  12, "866":  11, "846":  10,
    "868":  10, "848":  1,  "850":  10,

    # Jelgava / Olaine
    "6871": 1,  "6701": 1,  "6873": 1,  "6705": 5,
    "6875": 5,  "6709": 5,  "6877": 5,  "6713": 5,
    "6715": 5,  "6717": 5,  "6719": 5,  "6721": 5,
    "6723": 5,  "721":  1,  "6725": 5,  "6729": 5,
    "6879": 1,  "6733": 5,  "6881": 1,  "6737": 5,
    "6883": 5,  "723":  1,  "6741": 5,  "6745": 5,
    "6747": 5,  "6749": 5,  "6751": 5,  "6753": 5,

    # International
    "891":  1,   # Vilnius
}

# ---------------------------------------------------------------------------
# Platform map  (track -> platform)
# ---------------------------------------------------------------------------

TRACK_TO_PLATFORM: dict[int, int] = {
    11: 1, 12: 1,
    10: 2, 1:  2,
    3:  3, 4:  3,
    5:  4,
}

# ---------------------------------------------------------------------------
# Construction overrides
# Fill in when rail works affect track assignments.
#
# Format:
# {
#   "date_from": date(2026, 5, 1),
#   "date_to":   date(2026, 5, 31),
#   "tracks": {
#       "6502": 4,   # normally 3, moved to 4 during works
#       ...
#   }
# }
# ---------------------------------------------------------------------------

CONSTRUCTION_OVERRIDES: list[dict] = [
    # Example (disabled — uncomment and fill dates when needed):
    # {
    #     "date_from": date(2026, 5, 1),
    #     "date_to":   date(2026, 5, 31),
    #     "tracks": {
    #         "6502": 4,
    #     }
    # },
]


def _construction_track(train_nr: str, today: date) -> int | None:
    """Return construction-period track if today is within any active range."""
    for override in CONSTRUCTION_OVERRIDES:
        if override["date_from"] <= today <= override["date_to"]:
            if train_nr in override["tracks"]:
                return override["tracks"][train_nr]
    return None


# ---------------------------------------------------------------------------
# Default track by train number pattern
# ---------------------------------------------------------------------------

# Tracks used for multi-platform groups — chosen to avoid soon-departing trains
_SKULTE_TRACKS    = [10, 11]
_NORTH_DIESEL_TRACKS = [1, 10, 11]   # Daugavpils diesel, Gulbene, Madona …
_VALMIERA_TRACKS  = [1, 10, 11, 12]  # Valmiera, Sigulda, Cēsis, Valga


def _pick_free_track(candidates: list[int], soon_occupied: set[int]) -> int:
    """
    Pick a random track from candidates, preferring those not in soon_occupied.
    Falls back to any candidate if all are occupied.
    """
    free = [t for t in candidates if t not in soon_occupied]
    pool = free if free else candidates
    return random.choice(pool)


def default_track(train_nr: str, dest: str, soon_occupied: set[int] | None = None) -> int:
    """
    Determine track from train number pattern and/or destination.
    soon_occupied: set of track numbers that have a departure within ~5 minutes.
    """
    occ = soon_occupied or set()
    nr  = train_nr.strip()

    # 4-digit electric trains starting with 6
    if len(nr) == 4 and nr.startswith("6") and nr.isdigit():
        second = nr[1]
        if second == "1":                        # Skulte direction
            return _pick_free_track(_SKULTE_TRACKS, occ)
        if second == "2":                        # Ogre / Aizkraukle
            return 4
        if second in ("3", "4", "5"):            # Tukums / Dubulti / Imanta
            return 3
        if second == "7":                        # Jelgava / Olaine electric
            return 5
        if second == "8":                        # Jelgava / Olaine diesel (construction)
            return 5

    # 3-digit diesel trains starting with 7 or 8
    if len(nr) == 3 and nr.isdigit():
        first = nr[0]
        if first in ("7", "8"):
            # Distinguish by destination
            dest_lower = dest.lower()
            if any(d in dest_lower for d in
                   ["jelgava", "olaine", "ķemeri"]):
                return 5
            if any(d in dest_lower for d in
                   ["sigulda", "valmiera", "cēsis", "valga", "tartu",
                    "tallinn", "tallina"]):
                return _pick_free_track(_VALMIERA_TRACKS, occ)
            if any(d in dest_lower for d in
                   ["daugavpils", "indra", "krāslava", "ludza",
                    "rēzekne", "zilupe", "gulbene", "madona",
                    "liepāja", "vilnius", "kaunas"]):
                return _pick_free_track(_NORTH_DIESEL_TRACKS, occ)
            # Unknown diesel — safe fallback
            return _pick_free_track(_NORTH_DIESEL_TRACKS, occ)

    # Truly unknown — use track 1
    return 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_track(train_nr: str, dest: str,
              soon_occupied: set[int] | None = None,
              today: date | None = None) -> int:
    """
    Return the correct departure track for a given train.

    Priority:
      1. Construction override (if today is within an active period)
      2. Explicit TRACK_MAP entry
      3. default_track() by pattern / destination
    """
    today = today or date.today()

    # 1. Construction override
    c = _construction_track(train_nr, today)
    if c is not None:
        return c

    # 2. Explicit map
    if train_nr in TRACK_MAP:
        return TRACK_MAP[train_nr]

    # 3. Pattern default
    return default_track(train_nr, dest, soon_occupied)


def get_platform(track: int) -> int:
    """Return the platform number for a given track."""
    return TRACK_TO_PLATFORM.get(track, 2)  # default platform 2 if unknown
