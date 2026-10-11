"""Verified IBNR/EVA identifiers for frequently used stations.

These are Deutsche Bahn station numbers, not timetable or live status data.
They allow configuring supported routes even if the location-search API fails.
"""
from __future__ import annotations

import re

_STATIONS = {
    'göttingen': ('8000128', 'Göttingen'),
    'goettingen': ('8000128', 'Göttingen'),
    'göttingen hbf': ('8000128', 'Göttingen'),
    'goettingen hbf': ('8000128', 'Göttingen'),
    'göttingen bahnhof': ('8000128', 'Göttingen'),
    'bahnhof göttingen': ('8000128', 'Göttingen'),
    'leinefelde': ('8010203', 'Leinefelde'),
    'leinefelde bahnhof': ('8010203', 'Leinefelde'),
    'bahnhof leinefelde': ('8010203', 'Leinefelde'),
}


def known_station(name: str) -> tuple[str, str] | None:
    """Return known station ID without network, or None for other inputs."""
    key = re.sub(r'\s+', ' ', name.strip()).casefold()
    return _STATIONS.get(key)


def normalize_station_name(name: str) -> str:
    """Unify common spelling variants, not unrelated platform/stop names."""
    clean = re.sub(r"\s+", " ", str(name).strip()).casefold()
    clean = clean.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    clean = re.sub(r"^(?:bahnhof)\s+", "", clean)
    clean = re.sub(r"\s+(?:hauptbahnhof|hbf|bahnhof)$", "", clean)
    return re.sub(r"[^a-z0-9]", "", clean)


def select_station(results: object, query: str) -> tuple[str, str] | None:
    """Match an unambiguous whole station name from stations or locations.

    /stations returns an object keyed by EVA IDs; /locations returns a list.
    Do not select the first fuzzy station (e.g. a suburb) on failure.
    """
    if isinstance(results, dict):
        candidates = list(results.values())
    elif isinstance(results, list):
        candidates = results
    else:
        return None
    wanted = normalize_station_name(query)
    if not wanted:
        return None
    exact: dict[str, tuple[str, str]] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("station", "stop", None):
            continue
        name, station_id = item.get("name"), item.get("id")
        if not (isinstance(name, str) and name.strip() and station_id is not None):
            continue
        # Strictly match the complete normalized label; never approximate
        # automatically if only part of a name was typed.
        if normalize_station_name(name) != wanted:
            continue
        clean_id = str(station_id).strip()
        if not clean_id.isdigit():
            continue
        exact[clean_id] = (clean_id, name.strip())
    return next(iter(exact.values())) if len(exact) == 1 else None
