"""Parse DBF/IRIS version-3 station boards.

The public DBF board is a near-term source, *not* a seven-day timetable.
All times supplied only as HH:MM are interpreted in the requested local day.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")


def norm(value: str | None) -> str:
    """Match names with spelling variations, without partial-line collisions."""
    value = (value or "").casefold().replace("ö", "oe").replace("ä", "ae").replace("ü", "ue")
    return re.sub(r"[^a-z0-9]", "", value)


def clock_on_day(value: object, near: datetime) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
        hour, minute = [int(x) for x in value.split(":")[:2]]
        day = near.astimezone(TZ).date()
        candidates = [
            datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ) + timedelta(days=delta)
            for delta in (-1, 0, 1)
        ]
        return min(candidates, key=lambda candidate: abs((candidate - near).total_seconds()))
    except (TypeError, ValueError):
        return None


def goes_to_destination(entry: dict, destination: str) -> bool:
    """Verify the destination, not just the RE 1 / RE 11 line."""
    expected = norm(destination)
    if not expected:
        return False
    if norm(entry.get("destination")) == expected:
        return True
    for stop in entry.get("route") or []:
        if isinstance(stop, dict):
            if norm(stop.get("name")) == expected and not stop.get("isCancelled"):
                return True
        elif isinstance(stop, str) and norm(stop) == expected:
            return True
    # The short 'via' field may carry the destination if route is omitted.
    via = entry.get("via") or []
    return any(norm(v if isinstance(v, str) else v.get("name")) == expected for v in via if isinstance(v, (str, dict)))


def matches_line(train: str | None, line: str) -> bool:
    """Recognise customer-facing RE lines in IRIS names, e.g. 'RE RE1'.

    The first RE in an IRIS name denotes the product class and the second
    indicates the displayed line. Compare line numbers as complete tokens;
    never allow RE1 to match RE11 or a train run such as RE16243.
    """
    if not isinstance(train, str) or not isinstance(line, str):
        return False
    target = re.fullmatch(r"RE\s*([0-9]+)", line.strip(), flags=re.IGNORECASE)
    if target:
        line_numbers = set(re.findall(
            r"(?<![A-Z0-9])RE\s*([0-9]+)(?![A-Z0-9])",
            train.upper(),
        ))
        return line_numbers == {target.group(1)}
    actual = re.sub(r"\s+", "", train).upper()
    expected = re.sub(r"\s+", "", line).upper()
    return bool(expected) and (actual == expected or actual.startswith(expected + "("))


def board_departure(entries: list[dict], line: str, destination: str, planned: datetime, tolerance: int) -> dict | None:
    matches = []
    inferred = []
    for entry in entries:
        if not isinstance(entry, dict) or not goes_to_destination(entry, destination):
            continue
        scheduled = clock_on_day(entry.get("scheduledDeparture"), planned)
        if scheduled is None:
            continue
        distance = abs((scheduled - planned).total_seconds())
        if distance > tolerance * 60:
            continue

        # Prefer the train's exact displayed line; the optional "line"
        # field from a DBF version may contain only the product class "RE".
        if matches_line(entry.get("train"), line) or matches_line(entry.get("line"), line):
            matches.append((distance, scheduled, entry))
        elif (
            # The IRIS board often displays a train run number such as
            # "RE 16243" instead of the passenger-facing line "RE 1".
            # Only use the timetable/destination heuristic for one
            # uniquely close regional service. Never assert line identity.
            re.fullmatch(r"RE\s*[0-9]{4,6}", str(entry.get("train") or ""), flags=re.IGNORECASE)
            and distance <= min(tolerance, 7) * 60
        ):
            inferred.append((distance, scheduled, entry))

    if matches:
        _, scheduled, entry = min(matches, key=lambda x: x[0])
        confidence = "exact"
    elif len(inferred) == 1:
        _, scheduled, entry = inferred[0]
        confidence = "time_destination_unconfirmed"
    else:
        return None
    delay = entry.get("delayDeparture")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        delay = None
    elif delay is not None:
        delay = int(round(delay))
    actual = scheduled + timedelta(minutes=delay) if delay is not None else None
    cancelled = bool(entry.get("isCancelled"))
    # An inferred match cannot serve as proof that the user's specific
    # RE 1 / RE 11 was cancelled.
    status = (
        "unknown" if cancelled and confidence != "exact"
        else "cancelled" if cancelled
        else "delayed" if delay is not None and delay >= 3
        else "on_time" if delay is not None else "scheduled"
    )
    return {
        "date": planned.date().isoformat(),
        "source": "DBF/IRIS-TTS",
        "line": line if confidence == "exact" else None,
        "observed_train": entry.get("train"),
        "line_match": confidence,
        "status": status,
        "observed_cancellation": cancelled,
        "scheduled_departure": scheduled.isoformat(),
        "predicted_departure": actual.isoformat() if actual else None,
        "departure_delay_minutes": delay,
        "platform": entry.get("platform"),
        "scheduled_platform": entry.get("scheduledPlatform"),
        "scheduled_arrival": None,
        "predicted_arrival": None,
        "arrival_delay_minutes": None,
        "trip_id": None,
        "turnaround": {"status": "not_checked", "risk": False},
    }


def incoming_candidates(
    entries: list[dict], line: str, outbound: datetime, max_turn: int,
) -> list[dict]:
    """Return *all* plausible inbound same-line trains, never a vehicle link."""
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict) or not matches_line(entry.get("train"), line):
            continue
        planned = clock_on_day(entry.get("scheduledArrival"), outbound)
        if planned is None:
            continue
        gap = (outbound - planned).total_seconds() / 60
        if not 0 <= gap <= max_turn:
            continue
        delay = entry.get("delayArrival")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)):
            delay = None
        predicted = planned + timedelta(minutes=delay) if delay is not None else None
        candidates.append({
            "line": {"name": line},
            "plannedWhen": planned.isoformat(),
            "when": predicted.isoformat() if predicted else None,
            "delay": delay * 60 if delay is not None else None,
            "cancelled": bool(entry.get("isCancelled")),
            "observed_train": entry.get("train"),
        })
    return sorted(candidates, key=lambda item: item["plannedWhen"], reverse=True)


def board_incoming(
    entries: list[dict], line: str, outbound: datetime, max_turn: int,
) -> dict | None:
    """Legacy helper: latest candidate, NOT confirmed same-vehicle working."""
    candidates = incoming_candidates(entries, line, outbound, max_turn)
    return candidates[0] if candidates else None


def match_inbound_origin(
    entries: list[dict],
    *,
    line: str,
    destination: str,
    arrival: datetime,
    min_travel_minutes: int = 20,
    max_travel_minutes: int = 55,
) -> dict:
    """Corroborate an inbound arrival against the opposite station board.

    Only a unique matching line, destination and physically plausible time
    difference is returned. These two station announcements alone never
    prove an actual vehicle circulation.
    """
    matches = []
    for item in entries:
        if not isinstance(item, dict) or not matches_line(item.get("train"), line):
            continue
        if not goes_to_destination(item, destination):
            continue
        departure = clock_on_day(item.get("scheduledDeparture"), arrival)
        if departure is None:
            continue
        travel = (arrival - departure).total_seconds() / 60
        if not min_travel_minutes <= travel <= max_travel_minutes:
            continue
        raw_delay = item.get("delayDeparture")
        delay = (
            round(raw_delay) if isinstance(raw_delay, (int, float))
            and not isinstance(raw_delay, bool) else None
        )
        matches.append({
            "departure_planned": departure.isoformat(),
            "departure_predicted": (
                (departure + timedelta(minutes=delay)).isoformat()
                if delay is not None else None
            ),
            "departure_delay_minutes": delay,
            "scheduled_travel_minutes": round(travel),
            "train": item.get("train"),
        })
    if len(matches) == 1:
        return {
            "status": "plausible",
            "count": 1,
            **matches[0],
            "confirmed_vehicle": False,
        }
    return {
        "status": "ambiguous" if matches else "not_found",
        "count": len(matches),
        "confirmed_vehicle": False,
    }
