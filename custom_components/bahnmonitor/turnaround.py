"""Infer plausible terminal turnarounds for a monitored rail corridor.

Never claim that two separate timetable entries prove the same physical train.
The detector observes both endpoints, requires explicit terminal evidence and
the same public line, and reports ambiguity instead of inventing a vehicle ID.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any

from .dbf import clock_on_day, goes_to_destination, matches_line, norm

PRODUCTS = ("RE", "RB", "IRE", "ICE", "IC", "EC", "S", "MEX", "ALX", "TLX", "RS")


def passenger_line(item: dict, filters: tuple[str, ...] = ()) -> str | None:
    """Read a passenger-facing line, excluding ambiguous train run numbers."""
    name = str(item.get("train") or "")
    explicit = str(item.get("line") or "")
    if filters:
        return next((
            expected for expected in filters
            if matches_line(name, expected) or matches_line(explicit, expected)
        ), None)
    for value in (name, explicit):
        for product in PRODUCTS:
            # IRIS uses strings such as 'RE RE1', 'RB RB52' and 'ICE 70'.
            match = re.search(
                rf"(?<![A-Z0-9]){product}\s*(?:{product}\s*)?(\d+[A-Z]?)(?![A-Z0-9])",
                value.upper(),
            )
            if not match:
                continue
            number = match.group(1)
            # RE 16243 denotes a train run, not necessarily line RE 16243.
            if product in ("RE", "RB", "S", "MEX", "RS", "TLX", "ALX") and len(number) >= 4:
                continue
            return f"{product} {number}"
    return None


def _stop_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("name") or (value.get("stop") or {}).get("name") or "")
    return ""


def _stop_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_stop_name(item) for item in value if _stop_name(item)]


def _terminal_evidence(arrival: dict, station: str) -> str | None:
    """Terminal must be explicit, not merely the destination of our query."""
    published = _stop_name(arrival.get("destination"))
    if published:
        # Published destination explicitly continuing beyond this station
        # overrides an incomplete/truncated station-board route list.
        return "published_destination" if norm(published) == norm(station) else None
    route = _stop_names(arrival.get("route"))
    if route and norm(route[-1]) == norm(station):
        return "last_route_stop"
    return None


def _origin_evidence(arrival: dict, other: str) -> bool:
    if norm(_stop_name(arrival.get("origin"))) == norm(other):
        return True
    route = _stop_names(arrival.get("route"))
    return any(norm(stop) == norm(other) for stop in route[:-1])


def _delay(raw: object) -> int | None:
    return round(raw) if isinstance(raw, (float, int)) and not isinstance(raw, bool) and -90 <= raw <= 360 else None


def _key(item: dict) -> str:
    return "|".join((item["station"], item["line"], item["arrival_scheduled"],
                     item["departure_scheduled"]))


def detect_turnarounds(
    *,
    now: datetime,
    endpoints: list[dict],
    lines: tuple[str, ...] = (),
    min_turn_minutes: int = 8,
    max_turn_minutes: int = 60,
) -> dict:
    """Detect plausible line turns at each station within the real-time board window.

    endpoints: [{'station': str, 'other': str, 'arrivals': [...],
                 'departures': [...], 'other_departures': [...]}].
    The opposite station departures can corroborate the inbound journey but do
    not establish the actual vehicle identity. No historic train extrapolation.
    """
    results: list[dict] = []
    errors: list[str] = []
    min_turn = max(0, min(30, int(min_turn_minutes)))
    max_turn = max(10, min(120, int(max_turn_minutes)))
    coverage = 0

    for endpoint in endpoints:
        station = endpoint["station"]
        other = endpoint["other"]
        arr_board = endpoint.get("arrivals")
        dep_board = endpoint.get("departures")
        if not isinstance(arr_board, list) or not isinstance(dep_board, list):
            errors.append(f"{station}: Ankunfts- oder Abfahrtstafel fehlt")
            continue
        coverage += 1
        inbound = []
        outbound = []
        for row in arr_board:
            if not isinstance(row, dict) or row.get("isCancelled"):
                continue
            line = passenger_line(row, lines)
            terminal = _terminal_evidence(row, station)
            arrival = clock_on_day(row.get("scheduledArrival"), now)
            if not (line and terminal and arrival and
                    now - timedelta(minutes=90) <= arrival <= now + timedelta(minutes=90)):
                continue
            inbound.append((row, line, arrival, terminal))
        for row in dep_board:
            if not isinstance(row, dict) or row.get("isCancelled"):
                continue
            line = passenger_line(row, lines)
            when = clock_on_day(row.get("scheduledDeparture"), now)
            if not (line and when and goes_to_destination(row, other) and
                    now - timedelta(minutes=90) <= when <= now + timedelta(minutes=120)):
                continue
            outbound.append((row, line, when))

        # One-to-one uniqueness: never silently pick from two possible incoming
        # trains or two possible return workings.
        proposed: list[dict] = []
        for a, line, arrival, terminal in inbound:
            for d, dep_line, departure in outbound:
                if line != dep_line:
                    continue
                gap = (departure - arrival).total_seconds() / 60
                if not 0 <= gap <= max_turn:
                    continue
                arrival_delay = _delay(a.get("delayArrival"))
                departure_delay = _delay(d.get("delayDeparture"))
                predicted_arrival = (
                    arrival + timedelta(minutes=arrival_delay)
                    if arrival_delay is not None else None
                )
                predicted_departure = (
                    departure + timedelta(minutes=departure_delay)
                    if departure_delay is not None else None
                )
                turnaround_minutes = (
                    round((departure - predicted_arrival).total_seconds() / 60)
                    if predicted_arrival else None
                )
                possible_delay = (
                    max(0, round((predicted_arrival + timedelta(minutes=min_turn)
                                  - departure).total_seconds() / 60))
                    if predicted_arrival else None
                )
                route_proven = _origin_evidence(a, other)
                proposed.append({
                    "station": station,
                    "line": line,
                    "incoming_route": f"{other} → {station}" if route_proven else None,
                    "outgoing_route": f"{station} → {other}",
                    "incoming_train": a.get("train"),
                    "outgoing_train": d.get("train"),
                    "arrival_scheduled": arrival.isoformat(),
                    "arrival_predicted": predicted_arrival.isoformat() if predicted_arrival else None,
                    "incoming_delay_minutes": arrival_delay,
                    "departure_scheduled": departure.isoformat(),
                    "departure_predicted": predicted_departure.isoformat() if predicted_departure else None,
                    "outgoing_delay_minutes": departure_delay,
                    "scheduled_turn_minutes": round(gap),
                    "predicted_turn_minutes": turnaround_minutes,
                    "minimum_turn_minutes": min_turn,
                    "potential_delay_minutes_if_same_vehicle": possible_delay,
                    "possible_followup_delay": possible_delay > 0 if possible_delay is not None else None,
                    "terminal_evidence": terminal,
                    "inbound_route_evidence": "published_route" if route_proven else "unverified",
                    "vehicle_assignment_confirmed": False,
                    "source": "DBF/IRIS-TTS",
                    "status": "candidate",
                })

        incoming_counts: dict[tuple[str, str], int] = {}
        outgoing_counts: dict[tuple[str, str], int] = {}
        for candidate in proposed:
            key_in = (candidate["line"], candidate["arrival_scheduled"])
            key_out = (candidate["line"], candidate["departure_scheduled"])
            incoming_counts[key_in] = incoming_counts.get(key_in, 0) + 1
            outgoing_counts[key_out] = outgoing_counts.get(key_out, 0) + 1

        for candidate in proposed:
            unique = (
                incoming_counts[(candidate["line"], candidate["arrival_scheduled"])] == 1
                and outgoing_counts[(candidate["line"], candidate["departure_scheduled"])] == 1
            )
            candidate["status"] = "plausible" if unique else "ambiguous"
            candidate["confidence"] = (
                "corroborated" if unique and candidate["inbound_route_evidence"] == "published_route"
                else "indicative" if unique else "ambiguous"
            )
            # Never advertise an unverified train origin as a real incoming
            # service from the monitored endpoint.
            if not unique:
                candidate["possible_followup_delay"] = None
                candidate["potential_delay_minutes_if_same_vehicle"] = None
            results.append(candidate)

    unique_results = { _key(item): item for item in results }
    candidates = sorted(
        unique_results.values(),
        key=lambda row: (row["departure_scheduled"], row["station"], row["line"]),
    )[:12]
    return {
        "status": (
            "plausible" if any(row["status"] == "plausible" for row in candidates)
            else "ambiguous" if candidates else
            "not_found" if coverage == len(endpoints) else "source_unavailable"
        ),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "stations": sorted({row["station"] for row in candidates if row["status"] == "plausible"}),
        "possible_followup_delay": any(row["possible_followup_delay"] is True for row in candidates),
        "source_status": "online" if coverage == len(endpoints) else "partial" if coverage else "unavailable",
        "source_errors": errors,
        "last_evaluated": now.isoformat(),
        "vehicle_assignment_confirmed": False,
        "note": (
            "Automatische Wendevermutung aus Endbahnhof, Linie und Sollzeiten. "
            "Eine identische Fahrzeuggarnitur wird nicht nachgewiesen; "
            "Folgeverspätungen sind bedingte Rechenbeispiele."
        ),
    }
