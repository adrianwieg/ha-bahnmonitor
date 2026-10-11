"""Current rail departures that actually traverse the monitored endpoints.

This is not a complete daily timetable: IRIS station boards show a limited
near-term window. A missing train must never be interpreted as cancellation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .dbf import clock_on_day, delay_messages, goes_to_destination, norm
from .turnaround import passenger_line


def collect_departures(
    board: list[dict],
    *,
    now: datetime,
    origin: str,
    destination: str,
    direction: str,
    lines: tuple[str, ...] = (),
    lookback_minutes: int = 70,
    lookahead_minutes: int = 180,
) -> list[dict]:
    """Normalize every visible matching train, including punctual trains."""
    result = []
    for item in board:
        if not isinstance(item, dict) or not goes_to_destination(item, destination):
            continue
        line = passenger_line(item, lines)
        if line is None:
            continue
        planned = clock_on_day(item.get("scheduledDeparture"), now)
        if planned is None:
            continue
        if not (now - timedelta(minutes=lookback_minutes)
                <= planned <= now + timedelta(minutes=lookahead_minutes)):
            continue
        raw_delay = item.get("delayDeparture")
        delay = (
            round(raw_delay) if isinstance(raw_delay, (float, int))
            and not isinstance(raw_delay, bool) and -90 <= raw_delay <= 360
            else None
        )
        predicted = planned + timedelta(minutes=delay) if delay is not None else None
        cancelled = bool(item.get("isCancelled"))
        remarks = delay_messages(item)
        result.append({
            "origin": origin,
            "destination": destination,
            "direction": direction,
            "line": line,
            "train": item.get("train"),
            "planned_departure": planned.isoformat(),
            "predicted_departure": predicted.isoformat() if predicted else None,
            "delay_minutes": delay,
            "cancelled": cancelled,
            "status": (
                "cancelled" if cancelled
                else "delayed" if delay is not None and delay >= 3
                else "on_time" if delay is not None else "scheduled"
            ),
            "delay_reason": remarks[0]["text"] if remarks else None,
            "delay_reasons": remarks,
            "platform": item.get("platform"),
            "scheduled_platform": item.get("scheduledPlatform"),
            "source": "DBF/IRIS-TTS",
            "observed_at": now.isoformat(),
        })
    return sorted(
        result,
        key=lambda row: (row["planned_departure"], row["line"]),
    )



def collect_arrivals(
    board: list[dict],
    *,
    now: datetime,
    origin: str,
    destination: str,
    direction: str,
    lines: tuple[str, ...] = (),
    lookback_minutes: int = 70,
    lookahead_minutes: int = 180,
) -> list[dict]:
    """Only arrivals whose published origin or route includes the other station."""
    rows = []
    for item in board:
        if not isinstance(item, dict):
            continue
        route = item.get("route") or []
        origin_field = item.get("origin")
        origin_name = (
            origin_field.get("name") if isinstance(origin_field, dict)
            else origin_field if isinstance(origin_field, str) else None
        )
        stops = [
            point.get("name") if isinstance(point, dict) else point
            for point in route if isinstance(point, (str, dict))
        ]
        if not (
            norm(origin_name) == norm(origin)
            or any(norm(stop) == norm(origin) for stop in stops[:-1])
        ):
            continue
        line = passenger_line(item, lines)
        planned = clock_on_day(item.get("scheduledArrival"), now)
        if line is None or planned is None:
            continue
        if not (now - timedelta(minutes=lookback_minutes)
                <= planned <= now + timedelta(minutes=lookahead_minutes)):
            continue
        raw_delay = item.get("delayArrival")
        delay = (
            round(raw_delay) if isinstance(raw_delay, (int, float))
            and not isinstance(raw_delay, bool) and -90 <= raw_delay <= 360
            else None
        )
        predicted = planned + timedelta(minutes=delay) if delay is not None else None
        cancelled = bool(item.get("isCancelled"))
        remarks = delay_messages(item)
        rows.append({
            "origin": origin, "destination": destination, "direction": direction,
            "line": line, "train": item.get("train"),
            "planned_arrival": planned.isoformat(),
            "predicted_arrival": predicted.isoformat() if predicted else None,
            "delay_minutes": delay,
            "status": (
                "cancelled" if cancelled
                else "delayed" if delay is not None and delay >= 3
                else "on_time" if delay is not None else "scheduled"
            ),
            "cancelled": cancelled,
            "delay_reason": remarks[0]["text"] if remarks else None,
            "delay_reasons": remarks,
            "platform": item.get("platform"),
            "source": "DBF/IRIS-TTS",
            "observed_at": now.isoformat(),
        })
    return sorted(rows, key=lambda row: (row["planned_arrival"], row["line"]))


def summarise_departures(
    items: list[dict],
    *,
    now: datetime,
    sources_ok: list[str],
    arrivals: list[dict] | None = None,
) -> dict:
    """Keep the two directions together without treating missing boards as green."""
    rows = sorted(items, key=lambda x: x["planned_departure"])
    arrival_rows = sorted(arrivals or [], key=lambda x: x["planned_arrival"])
    future = [
        row for row in rows
        if (
            row["predicted_departure"] or row["planned_departure"]
        ) >= now.isoformat() and not row["cancelled"]
    ]
    next_departure = next(
        (row for row in future if row["direction"] == "same"), None
    )
    reverse_departure = next(
        (row for row in future if row["direction"] == "reverse"), None
    )
    incoming = [
        row for row in arrival_rows
        if (row["predicted_arrival"] or row["planned_arrival"]) >= now.isoformat()
        and not row["cancelled"]
    ]
    return {
        "departures": rows[:50],
        "arrivals": arrival_rows[:50],
        "arrival_count": len(arrival_rows),
        "next_arrival": next((row for row in incoming if row["direction"] == "same"), None),
        "next_reverse_arrival": next((row for row in incoming if row["direction"] == "reverse"), None),
        "departure_count": len(rows),
        "next_departure": next_departure,
        "next_reverse_departure": reverse_departure,
        "source_status": (
            "online" if len(sources_ok) == 2 else
            "partial" if sources_ok else "unavailable"
        ),
        "source": "DBF/IRIS-TTS",
        "scope": "Aktuelle Stationstafeln, keine garantierte vollständige Tagesübersicht",
    }
