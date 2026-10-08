"""Evidence-based disruption context from earlier trains on the same corridor.

Only already-scheduled departures on RE 1 / RE 11 are considered.
A corridor pattern is never evidence of the same physical train or proof that
a future train will be delayed. Observations are intentionally kept in RAM.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

from .dbf import clock_on_day, goes_to_destination, matches_line


LINES = ("RE 1", "RE 11")
MAX_PREVIOUS = 3
MAX_AGE = timedelta(hours=4)


def collect_previous(
    board: list[dict],
    *,
    direction: str,
    origin: str,
    destination: str,
    planned: datetime,
    now: datetime,
    lines: tuple[str, ...] = LINES,
) -> list[dict]:
    """Extract trains that have already been scheduled to depart.

    The board typically covers only 60 past minutes. Repeated successful
    samples can be accumulated by the coordinator, up to four hours.
    """
    collected = []
    upper = min(now, planned)
    lower = planned - MAX_AGE
    for item in board:
        if not isinstance(item, dict) or not goes_to_destination(item, destination):
            continue
        line = next(
            (value for value in lines if matches_line(item.get("train"), value)),
            None,
        )
        if line is None:
            continue
        departure = clock_on_day(item.get("scheduledDeparture"), planned)
        if departure is None or not lower <= departure < upper:
            continue
        # A train planned one minute ago may not have departed; its displayed
        # delay is still an observation, not a confirmed actual departure.
        delay = item.get("delayDeparture")
        delay = (
            round(delay)
            if isinstance(delay, (float, int)) and not isinstance(delay, bool)
            and -90 <= delay <= 360
            else None
        )
        cancelled = bool(item.get("isCancelled"))
        if delay is None and not cancelled:
            continue
        observed = {
            "direction": direction,
            "origin": origin,
            "destination": destination,
            "line": line,
            "train": item.get("train"),
            "scheduled_departure": departure.isoformat(),
            "delay_minutes": delay,
            "cancelled": cancelled,
            "observed_at": now.isoformat(),
            "source": "DBF/IRIS-TTS",
        }
        collected.append(observed)
    return collected


def observation_key(item: dict) -> str:
    """Deduplicate the same train across repeated polls."""
    return "|".join([
        item["direction"],
        str(item["train"]),
        str(item["scheduled_departure"]),
    ])


def summarise(
    observations: list[dict],
    *,
    target: datetime,
    now: datetime,
    limit: int = MAX_PREVIOUS,
) -> dict:
    """Summarise both directions; no artificial certainty from sparse data."""
    limit = max(1, min(5, limit))
    lower = target - MAX_AGE
    latest = {}
    for item in observations:
        if item.get("direction") not in ("same", "reverse"):
            continue
        try:
            dep = datetime.fromisoformat(item["scheduled_departure"])
        except (ValueError, TypeError, KeyError):
            continue
        if not lower <= dep < min(target, now):
            continue
        if item.get("delay_minutes") is None and not item.get("cancelled"):
            continue
        key = observation_key(item)
        previous = latest.get(key)
        if previous is None or item.get("observed_at", "") > previous.get("observed_at", ""):
            latest[key] = item

    directions = {}
    selected = []
    for direction in ("same", "reverse"):
        rows = sorted(
            (item for item in latest.values() if item["direction"] == direction),
            key=lambda item: item["scheduled_departure"],
            reverse=True,
        )[:limit]
        selected.extend(rows)
        delays = [
            item["delay_minutes"] for item in rows
            if item.get("delay_minutes") is not None and not item["cancelled"]
        ]
        directions[direction] = {
            "count": len(rows),
            "delayed_count": sum(1 for delay in delays if delay >= 3),
            "cancelled_count": sum(bool(item["cancelled"]) for item in rows),
            "avg_delay_minutes": round(mean(delays), 1) if delays else None,
            "trains": rows,
        }

    total = len(selected)
    cancelled = sum(1 for item in selected if item["cancelled"])
    known_delays = [
        item["delay_minutes"] for item in selected
        if item.get("delay_minutes") is not None and not item["cancelled"]
    ]
    delayed = sum(1 for delay in known_delays if delay >= 3)
    strongly_delayed = sum(1 for delay in known_delays if delay >= 10)
    average = round(mean(known_delays), 1) if known_delays else None

    if total == 0:
        status, label = "no_data", "Keine Daten"
    elif total < 2:
        status, label = "limited", "Wenig Daten"
    elif cancelled or strongly_delayed >= 2:
        status, label = "high", "Stark gestört"
    elif delayed >= 1 or (average is not None and average >= 5):
        status, label = "elevated", "Auffällig"
    else:
        status, label = "normal", "Unauffällig"

    if total == 0:
        summary = "Noch keine auswertbaren früheren Fahrten im Beobachtungsfenster."
    else:
        summary = (
            f"{total} frühere Fahrt(en) erfasst: {delayed} mit mindestens 3 Min "
            f"Verspätung, {cancelled} Ausfall/Ausfälle."
        )
        if total < 2:
            summary += " Für eine Einschätzung sind noch zu wenige Daten vorhanden."
        else:
            summary += " Dies ist nur ein Streckenindikator, keine Verspätungsprognose."

    return {
        "status_code": status,
        "status": label,
        "summary": summary,
        "sample_count": total,
        "delayed_count": delayed,
        "cancelled_count": cancelled,
        "average_delay_minutes": average,
        "same_direction": directions["same"],
        "reverse_direction": directions["reverse"],
        "window_start": lower.isoformat(),
        "target_departure": target.isoformat(),
        "last_evaluated": now.isoformat(),
        "max_trains_per_direction": limit,
        "confidence": "limited" if total < 2 else "indicative",
        "vehicle_assignment_confirmed": False,
    }
