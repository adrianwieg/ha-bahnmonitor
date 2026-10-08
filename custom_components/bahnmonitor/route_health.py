"""Daily RE 1 / RE 11 corridor context from IRIS station boards.

Always observe both directions during the day, regardless of the user's
configured departure time. This describes today's corridor, NOT a reliable
prediction for an afternoon train and NOT a vehicle-circulation match.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

from .dbf import clock_on_day, goes_to_destination, matches_line

LINES = ("RE 1", "RE 11")
MAX_PREVIOUS = 3
UPCOMING_MINUTES = 45


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _normalise_entry(
    item: dict,
    *,
    direction: str,
    origin: str,
    destination: str,
    now: datetime,
) -> dict | None:
    if not isinstance(item, dict) or not goes_to_destination(item, destination):
        return None
    line = next((line for line in LINES if matches_line(item.get("train"), line)), None)
    if line is None:
        return None
    when = clock_on_day(item.get("scheduledDeparture"), now)
    if when is None:
        return None
    value = item.get("delayDeparture")
    delay = (
        round(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and -90 <= value <= 360
        else None
    )
    cancelled = bool(item.get("isCancelled"))
    return {
        "direction": direction,
        "origin": origin,
        "destination": destination,
        "line": line,
        "train": item.get("train"),
        "scheduled_departure": when.isoformat(),
        "predicted_departure": (
            (when + timedelta(minutes=delay)).isoformat()
            if delay is not None else None
        ),
        "delay_minutes": delay,
        "cancelled": cancelled,
        "observed_at": now.isoformat(),
        "source": "DBF/IRIS-TTS",
    }


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
    """Observed trains scheduled earlier TODAY, even before a 16:09 departure.

    A scheduled departure in the past does not prove the train actually left;
    these are reported station-board statuses, not measured running history.
    'planned' remains for compatibility with existing callers.
    """
    rows = []
    lower = _day_start(now)
    for item in board:
        record = _normalise_entry(
            item, direction=direction, origin=origin,
            destination=destination, now=now,
        )
        if record is None or record["line"] not in lines:
            continue
        departure = datetime.fromisoformat(record["scheduled_departure"])
        if lower <= departure < now and (
            record["delay_minutes"] is not None or record["cancelled"]
        ):
            record["observation_type"] = "earlier_today"
            rows.append(record)
    return rows


def collect_upcoming(
    board: list[dict],
    *,
    direction: str,
    origin: str,
    destination: str,
    now: datetime,
    minutes: int = UPCOMING_MINUTES,
) -> list[dict]:
    """Current near-future departure forecasts, separate from earlier trains."""
    records = []
    for item in board:
        record = _normalise_entry(
            item, direction=direction, origin=origin,
            destination=destination, now=now,
        )
        if record is None:
            continue
        departure = datetime.fromisoformat(record["scheduled_departure"])
        if now <= departure <= now + timedelta(minutes=minutes) and (
            record["delay_minutes"] is not None or record["cancelled"]
        ):
            record["observation_type"] = "upcoming_forecast"
            records.append(record)
    return records


def observation_key(item: dict) -> str:
    """Deduplicate repeated observations of the same scheduled train."""
    return "|".join((
        str(item["direction"]),
        str(item["train"]),
        str(item["scheduled_departure"]),
    ))


def summarise(
    observations: list[dict],
    *,
    target: datetime,
    now: datetime,
    limit: int = MAX_PREVIOUS,
    upcoming: list[dict] | None = None,
) -> dict:
    """Summarise TODAY's previous and upcoming trains in both directions.

    A pre-trip route state is deliberately distinct from a specific train's
    expected delay. Old observations are never used across calendar days.
    """
    limit = max(1, min(5, limit))
    day_start = _day_start(now)
    latest = {}
    for row in observations:
        if row.get("direction") not in ("same", "reverse"):
            continue
        try:
            scheduled = datetime.fromisoformat(row["scheduled_departure"])
        except (KeyError, TypeError, ValueError):
            continue
        if not day_start <= scheduled < now:
            continue
        if row.get("delay_minutes") is None and not row.get("cancelled"):
            continue
        key = observation_key(row)
        previous = latest.get(key)
        if previous is None or row.get("observed_at", "") > previous.get("observed_at", ""):
            latest[key] = row

    upcoming_unique = {}
    for item in upcoming or []:
        if item.get("direction") not in ("same", "reverse"):
            continue
        try:
            scheduled = datetime.fromisoformat(item["scheduled_departure"])
        except (KeyError, TypeError, ValueError):
            continue
        if not now <= scheduled <= now + timedelta(minutes=UPCOMING_MINUTES):
            continue
        if item.get("delay_minutes") is None and not item.get("cancelled"):
            continue
        upcoming_unique[observation_key(item)] = item

    # Scheduled time being in the past is NOT proof of departure.
    # If a train was due at 08:43 with +45 min, at 08:44 it is still
    # predicted for 09:28. Keep it in "awaiting departure", not history.
    awaiting = {}
    for key, row in list(latest.items()):
        delay = row.get("delay_minutes")
        if row.get("cancelled") or isinstance(delay, bool) or not isinstance(delay, (int, float)):
            continue
        planned_when = datetime.fromisoformat(row["scheduled_departure"])
        predicted = planned_when + timedelta(minutes=delay)
        if predicted > now:
            pending = dict(row)
            pending["predicted_departure"] = predicted.isoformat()
            pending["observation_type"] = "awaiting_departure"
            awaiting[key] = pending
            del latest[key]

    directions = {}
    selected = []
    upcoming_rows = []
    overdue_rows = []
    for direction in ("same", "reverse"):
        previous_rows = sorted(
            (row for row in latest.values() if row["direction"] == direction),
            key=lambda row: row["scheduled_departure"],
            reverse=True,
        )[:limit]
        pending_rows = sorted(
            (row for row in awaiting.values() if row["direction"] == direction),
            key=lambda row: row["scheduled_departure"],
            reverse=True,
        )[:limit]
        future_rows = sorted(
            (row for row in upcoming_unique.values() if row["direction"] == direction),
            key=lambda row: row["scheduled_departure"],
        )[:limit]
        current_rows = pending_rows + future_rows
        selected.extend(previous_rows)
        upcoming_rows.extend(current_rows)
        overdue_rows.extend(pending_rows)
        valid = [
            row["delay_minutes"] for row in previous_rows
            if row.get("delay_minutes") is not None and not row["cancelled"]
        ]
        directions[direction] = {
            "count": len(previous_rows),
            "delayed_count": sum(v >= 3 for v in valid),
            "cancelled_count": sum(bool(row["cancelled"]) for row in previous_rows),
            "avg_delay_minutes": round(mean(valid), 1) if valid else None,
            "trains": previous_rows,
            "awaiting_departures": pending_rows,
            "current_departures": current_rows,
        }

    all_relevant = selected + upcoming_rows
    delayed = sum(
        row.get("delay_minutes") is not None
        and row["delay_minutes"] >= 3 and not row["cancelled"]
        for row in all_relevant
    )
    strong = sum(
        row.get("delay_minutes") is not None
        and row["delay_minutes"] >= 10 and not row["cancelled"]
        for row in all_relevant
    )
    cancelled = sum(bool(row["cancelled"]) for row in all_relevant)
    previous_delays = [
        row["delay_minutes"] for row in selected
        if row.get("delay_minutes") is not None and not row["cancelled"]
    ]
    sample = len(selected)
    if not all_relevant:
        code, label = "no_data", "Noch keine Beobachtungen"
    elif cancelled or strong >= 2:
        code, label = "high", "Stark gestört"
    elif delayed:
        code, label = "elevated", "Auffällig"
    elif sample < 2:
        code, label = "limited", "Wenig Daten"
    else:
        code, label = "normal", "Unauffällig"

    if not all_relevant:
        description = "Bisher keine auswertbaren RE-1-/RE-11-Meldungen von heute."
    else:
        description = (
            f"Heute: {sample} Sollabfahrten mit vergangener Prognosezeit, "
            f"{len(overdue_rows)} trotz verstrichener Sollzeit noch erwartete Züge "
            f"und {len(upcoming_rows) - len(overdue_rows)} bevorstehende Sollabfahrten "
            f"berücksichtigt; {delayed} verspätet, {cancelled} ausgefallen."
        )
        if sample < 2:
            description += " Datenlage begrenzt."
        description += " Keine gesicherte Prognose für deine eigene Zugfahrt."

    # Explicitly disclose WHY the indicator is elevated. Separate earlier
    # trains from short-term predictions, to avoid misrepresenting a future
    # scheduled departure as an already observed delay.
    reasons = []
    trigger_trains = sorted(
        [row for row in all_relevant
         if row.get("cancelled")
         or (isinstance(row.get("delay_minutes"), (int, float))
             and row["delay_minutes"] >= 3)],
        key=lambda row: row["scheduled_departure"],
    )
    for row in trigger_trains:
        ts = datetime.fromisoformat(row["scheduled_departure"]).strftime("%H:%M")
        route = (
            "Hinrichtung" if row["direction"] == "same"
            else "Gegenrichtung"
        )
        if row.get("observation_type") == "awaiting_departure":
            predicted = datetime.fromisoformat(
                row["predicted_departure"]
            ).strftime("%H:%M")
            observation_label = f"Sollzeit vorbei, Prognose {predicted}"
        elif row in upcoming_rows:
            observation_label = "bevorstehende Abfahrtsprognose"
        else:
            observation_label = "Sollzeit und Prognosezeit vergangen"
        situation = (
            "Ausfall gemeldet" if row["cancelled"]
            else f"+{row['delay_minutes']} Min gemeldet"
        )
        reasons.append(
            f"{ts} {row['line']} ({route}, {observation_label}): {situation}"
        )
    if reasons:
        cause = "; ".join(reasons[:6])
    elif all_relevant:
        cause = "Keine Verspätung ab 3 Minuten und kein Ausfall in den erfassten Meldungen."
    else:
        cause = "Noch keine auswertbaren Meldungen vorhanden."

    last_seen = max(
        (row.get("observed_at", "") for row in latest.values()), default=None,
    )
    return {
        "status_code": code,
        "status": label,
        "summary": description,
        "sample_count": sample,
        "current_count": len(upcoming_rows),
        "awaiting_count": len(overdue_rows),
        "upcoming_scheduled_count": len(upcoming_rows) - len(overdue_rows),
        "delayed_count": delayed,
        "cancelled_count": cancelled,
        "average_delay_minutes": round(mean(previous_delays), 1) if previous_delays else None,
        "previous_delayed_count": sum(
            row.get("delay_minutes") is not None
            and row["delay_minutes"] >= 3 and not row["cancelled"]
            for row in selected
        ),
        "forecast_delayed_count": sum(
            row.get("delay_minutes") is not None
            and row["delay_minutes"] >= 3 and not row["cancelled"]
            for row in upcoming_rows
        ),
        "maximum_delay_minutes": max(previous_delays, default=None),
        "reason": cause,
        "trigger_reasons": reasons[:6],
        "same_direction": directions["same"],
        "reverse_direction": directions["reverse"],
        "window_start": day_start.isoformat(),
        "target_departure": target.isoformat(),
        "last_evaluated": now.isoformat(),
        "last_observed_at": last_seen,
        "max_trains_per_direction": limit,
        "confidence": "limited" if sample < 2 else "indicative",
        "vehicle_assignment_confirmed": False,
        "monitoring_scope": "today_continuous",
    }
