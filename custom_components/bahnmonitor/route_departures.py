"""Current rail departures that actually traverse the monitored endpoints.

This is not a complete daily timetable: IRIS station boards show a limited
near-term window. A missing train must never be interpreted as cancellation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .dbf import clock_on_day, delay_messages, goes_to_destination
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


def summarise_departures(
    items: list[dict],
    *,
    now: datetime,
    sources_ok: list[str],
) -> dict:
    """Keep the two directions together without treating missing boards as green."""
    rows = sorted(items, key=lambda x: x["planned_departure"])
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
    return {
        "departures": rows[:50],
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
