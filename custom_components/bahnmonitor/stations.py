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
