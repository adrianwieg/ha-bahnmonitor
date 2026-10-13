"""Detect potentially related bus sections between monitored endpoints.

Public near-term station boards may show a bus only from A to an intermediate
stop C and a rail segment C -> B on the other endpoint's board. Report such a
candidate without inventing a through-service or a guaranteed transfer.

Neither a shared stop nor a bus carrying a rail-line label proves officially
designated SEV. Confirmation still requires an explicit disruption notice.
"""
from __future__ import annotations

import re
from .dbf import norm, matches_line, clock_on_day
from .turnaround import passenger_line


def _name(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        nested = value.get("stop")
        return str(value.get("name") or (
            nested.get("name") if isinstance(nested, dict) else ""
        ) or "").strip()
    return ""


def _path(row: dict, station: str, *, departure: bool) -> list[str]:
    """Keep ordered station names only in the relevant direction."""
    raw = row.get("route")
    stops = [_name(v) for v in raw] if isinstance(raw, list) else []
    stops = [stop for stop in stops if stop]
    station_key = norm(station)
    matches = [i for i, stop in enumerate(stops) if norm(stop) == station_key]
    if matches:
        return stops[matches[-1]:] if departure else stops[:matches[0] + 1]
    # A station board is evidence of the stop itself. Never assume other
    # stops beyond the published route where the current station is absent.
    return [station, *stops] if departure else [*stops, station]


def _bus_name(row: dict) -> str | None:
    raw = str(row.get("train") or row.get("line") or "").strip()
    return raw if re.search(r"(?<![A-Z])(?:Bus|SEV|Ersatzbus)\b", raw, re.I) else None


def _row_line(row: dict) -> str | None:
    return passenger_line(row)


def _is_filtered(row: dict, filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    return any(
        matches_line(str(row.get("train") or ""), line)
        or matches_line(str(row.get("line") or ""), line)
        for line in filters
    )


def find_partial_bus_sections(
    *,
    now,
    endpoints: list[dict],
    lines: tuple[str, ...] = (),
) -> dict:
    """Find endpoint-to-interchange bus + opposite endpoint rail evidence.

    endpoints = [
       {station: A, other: B, departures: station A departure board,
        arrivals: station A arrival board},
       {station: B, other: A, departures: station B departure board,
        arrivals: station B arrival board},
    ]
    A bus terminating at C is considered only if a separately observed train
    on the opposite endpoint's board also serves C. This is *structural*
    evidence; intermediate transfer time and official replacement designation
    remain unknown.
    """
    if len(endpoints) != 2:
        return {"status": "insufficient_data", "candidates": [],
                "reason": "Genau zwei Bahnhofs-Endpunkte erforderlich"}
    pairs = (
        ("same", endpoints[0], endpoints[1]),
        ("reverse", endpoints[1], endpoints[0]),
    )
    candidates = {}
    coverage = 0
    for direction, first, last in pairs:
        begin, end = first["station"], last["station"]
        departures = first.get("departures")
        arrivals = last.get("arrivals")
        if not isinstance(departures, list) or not isinstance(arrivals, list):
            continue
        coverage += 1
        # A bus near the beginning of a multi-leg corridor, observed as a
        # departure at the route origin.
        for bus in departures:
            if not isinstance(bus, dict) or not _bus_name(bus):
                continue
            bus_path = _path(bus, begin, departure=True)
            if len(bus_path) < 2 or norm(bus_path[0]) != norm(begin):
                continue
            interchange = bus_path[-1]
            if norm(interchange) in (norm(begin), norm(end)):
                continue
            for rail in arrivals:
                if not isinstance(rail, dict) or _bus_name(rail):
                    continue
                rail_path = _path(rail, end, departure=False)
                if not rail_path or norm(rail_path[-1]) != norm(end):
                    continue
                # A real departure between interchange and B, not a station
                # that is merely mentioned after B in a longer journey.
                if not any(norm(stop) == norm(interchange)
                           for stop in rail_path[:-1]):
                    continue
                _append_candidate(
                    candidates, now, bus, bus_path, rail, interchange,
                    direction, begin, end, "bus_from_origin", lines,
                )
                break

        # The bus can instead be the final leg into the destination;
        # the reverse station-board may show the train at origin.
        for bus in arrivals:
            if not isinstance(bus, dict) or not _bus_name(bus):
                continue
            bus_path = _path(bus, end, departure=False)
            if len(bus_path) < 2 or norm(bus_path[-1]) != norm(end):
                continue
            interchange = bus_path[0]
            if norm(interchange) in (norm(begin), norm(end)):
                continue
            for rail in departures:
                if not isinstance(rail, dict) or _bus_name(rail):
                    continue
                rail_path = _path(rail, begin, departure=True)
                if not any(norm(stop) == norm(interchange)
                           for stop in rail_path[1:]):
                    continue
                _append_candidate(
                    candidates, now, bus, bus_path, rail, interchange,
                    direction, begin, end, "bus_to_destination", lines,
                )
                break

    rows = sorted(
        candidates.values(),
        key=lambda x: (x["scheduled"] or "", x["direction"], x["train"]),
    )[:20]
    return {
        "status": (
            "candidates_found" if rows else
            "none_observed" if coverage == 2 else "partial" if coverage else "unavailable"
        ),
        "candidates": rows,
        "candidate_count": len(rows),
        "source_status": "online" if coverage == 2 else "partial" if coverage else "unavailable",
        "confirmed_sev": False,
        "note": (
            "Mögliche Bus-Teilstrecken: gemeinsamer Umsteigebahnhof mit "
            "einer Zugstrecke vom anderen Endpunkt. Weder SEV-Zuordnung "
            "noch Umsteigezeiten/Fahrzeugdurchbindung sind bestätigt."
        ),
    }


def _append_candidate(
    output: dict, now, bus: dict, bus_path: list[str], rail: dict,
    interchange: str, direction: str, begin: str, end: str, role: str,
    lines: tuple[str, ...],
) -> None:
    raw_time = bus.get(
        "scheduledDeparture" if role == "bus_from_origin" else "scheduledArrival"
    )
    planned = clock_on_day(raw_time, now)
    train = _bus_name(bus) or ""
    bus_line = _row_line(bus)
    rail_line = _row_line(rail)
    key = (direction, role, train, planned.isoformat() if planned else str(raw_time), interchange)
    if key in output:
        return
    output[key] = {
        "direction": direction,
        "monitored_route": f"{begin} → {end}",
        "train": train,
        "line": bus_line,
        "scheduled": planned.isoformat() if planned else str(raw_time or ""),
        "time_type": "departure" if role == "bus_from_origin" else "arrival",
        "bus_start": bus_path[0],
        "bus_end": bus_path[-1],
        "via": bus_path[:15],
        "interchange": interchange,
        "connecting_train": str(rail.get("train") or "")[:100],
        "connecting_line": rail_line,
        "role": role,
        "line_filter_match": _is_filtered(bus, lines),
        "confirmed_sev": False,
        "transfer_confirmed": False,
        "source": "DBF/IRIS-TTS",
    }
