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
    # Hauptbahnhof and Hbf are synonyms; omitting Hbf altogether is *not*
    # equivalent. "Berlin" must not silently resolve to "Berlin Hbf".
    clean = re.sub(r"\bhauptbahnhof\b", "hbf", clean)
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


def select_public_transport_stop(results: object, query: str) -> tuple[str, str] | None:
    """Resolve bus stops whose DB display names may reverse locality order.

    E.g. 'Bahnhof/ZOB, Göttingen' versus 'Göttingen, Bahnhof/ZOB'.
    These name variants must not be collapsed into a nearby railway station
    or any randomly picked autocomplete result.
    """
    direct = select_station(results, query)
    if direct is not None:
        return direct
    if not isinstance(results, list) or "," not in query:
        return None
    left, right = (v.strip() for v in query.split(",", 1))
    first, locality = normalize_station_name(left), normalize_station_name(right)
    if not first or not locality:
        return None
    # For compound municipal names the stop can include the primary locality
    # instead of the formal merged administrative name (Leinefelde-Worbis).
    locality_head = normalize_station_name(right.split("-")[0])
    matches = {}
    for item in results:
        if not isinstance(item, dict) or item.get("type") not in ("stop", None):
            continue
        name = item.get("name")
        sid = str(item.get("id") or "")
        if not isinstance(name, str) or not sid.isdigit():
            continue
        products = item.get("products")
        if isinstance(products, dict) and products.get("bus") is False:
            continue
        actual = normalize_station_name(name)
        if not actual:
            continue
        if actual == first or (
            first in actual and (locality in actual or locality_head in actual)
        ):
            matches[sid] = (sid, name)
    return next(iter(matches.values())) if len(matches) == 1 else None



def station_complex_bus_stop(
    results: object,
    station_name: str,
    *,
    rail_station_id: str | None = None,
) -> tuple[str, str] | None:
    """Identify one unambiguous bus terminal attached to the named station.

    This models station COMPLEXES, not exact string equality: the same user
    route 'Leinefelde -> Göttingen' may use rail platforms and a Bus Bahnhof/ZOB.
    When no *uniquely identifiable* bus stop is published, return None so the
    coordinator reports ambiguity/unknown rather than selecting a suburb.
    """
    if not isinstance(results, list):
        return None
    # Do not assume "city" and "city Hbf" mean the same thing. Resolve
    # canonical rail station names separately; this is for a bus terminal
    # specifically collocated by its name with the configured rail station.
    key = normalize_station_name(station_name)
    if not key:
        return None
    # The canonical station name can have an explicit Hbf suffix.
    key = re.sub(r"hbf$", "", key)
    matches: dict[str, tuple[str, str]] = {}
    for item in results:
        if not isinstance(item, dict) or item.get("type") not in ("stop", None):
            continue
        sid = str(item.get("id") or "").strip()
        name = item.get("name")
        if not sid.isdigit() or not isinstance(name, str) or not name.strip():
            continue
        if rail_station_id and sid == str(rail_station_id):
            continue
        products = item.get("products")
        if isinstance(products, dict) and products.get("bus") is False:
            continue
        # Accept explicitly station-based bus termini only. The municipality
        # alone is never sufficient (no 'Göttingen ZOB Süd' to 'Göttingen').
        parts = [p.strip() for p in name.split(",") if p.strip()]
        if not parts:
            continue
        terminal_tokens = r"(?:bahnhof|hauptbahnhof|hbf|busbahnhof|zob|bahnhofsplatz)"
        # Match 'Leinefelde Bahnhof, Leinefelde-Worbis' / 'Leinefelde, Bahnhof'
        # / 'Bahnhof/ZOB, Göttingen'. Token boundary avoids 'Bad Göttingen'.
        def is_station_terminal(fragment: str) -> bool:
            return bool(re.search(
                rf"(?<![a-z]){terminal_tokens}(?![a-z])", fragment.casefold()
            ))

        def name_matches(fragment: str) -> bool:
            normalized = normalize_station_name(fragment)
            if normalized == key:
                return True
            # The place name immediately followed by a station facility,
            # not an unrelated town like 'Bad Leinefelde'.
            for suffix in ("bahnhof", "hauptbahnhof", "hbf",
                           "busbahnhof", "zob", "bahnhofsplatz"):
                if normalized == key + suffix:
                    return True
            return False

        same_site = (
            (name_matches(parts[0]) and is_station_terminal(name))
            or (
                len(parts) >= 2
                and any(name_matches(p) for p in parts[1:])
                and is_station_terminal(parts[0])
            )
        )
        if same_site:
            matches[sid] = (sid, name.strip())
    return next(iter(matches.values())) if len(matches) == 1 else None
