"""Pure functions for matching services and estimating turnaround risk."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def matches_departure(item: dict, line: str, target: datetime, tolerance: int) -> bool:
    if normalize_line((item.get("line") or {}).get("name", "")) != normalize_line(line):
        return False
    scheduled = parse_time(item.get("plannedWhen") or item.get("when"))
    return scheduled is not None and abs((scheduled - target).total_seconds()) <= tolerance * 60


def cancellation(item: dict) -> bool:
    return bool(item.get("cancelled") or item.get("canceled"))


def delay_minutes(item: dict) -> int | None:
    delay = item.get("delay")
    if isinstance(delay, (int, float)):
        return round(delay / 60)
    planned, actual = parse_time(item.get("plannedWhen")), parse_time(item.get("when"))
    if planned and actual:
        return round((actual - planned).total_seconds() / 60)
    return None


def select_incoming(arrivals: list[dict], line: str, outbound: datetime, max_turn_minutes: int) -> dict | None:
    """Match *candidate* inbound service, never claim a physical vehicle coupling."""
    candidates = []
    for item in arrivals:
        if normalize_line((item.get("line") or {}).get("name", "")) != normalize_line(line):
            continue
        planned = parse_time(item.get("plannedWhen") or item.get("when"))
        if planned and timedelta(minutes=0) <= outbound - planned <= timedelta(minutes=max_turn_minutes):
            candidates.append((planned, item))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def evaluate_turnaround(incoming: dict | None, departure: datetime, min_turn: int) -> dict:
    if not incoming:
        return {"status": "unknown", "risk": False, "reason": "Keine passende Vorleistung gefunden"}
    planned = parse_time(incoming.get("plannedWhen") or incoming.get("when"))
    predicted = parse_time(incoming.get("when"))
    if not planned:
        return {"status": "unknown", "risk": False, "reason": "Ankunftszeit nicht verfügbar"}
    if cancellation(incoming):
        return {"status": "possible", "risk": True, "reason": "Mögliche Vorleistung fällt aus (nicht bestätigt)", "incoming_planned": planned.isoformat()}
    if not predicted:
        return {"status": "unknown", "risk": False, "reason": "Noch keine Ankunftsprognose"}
    remaining = (departure - predicted).total_seconds() / 60
    risk = remaining < min_turn
    earliest = predicted + timedelta(minutes=min_turn)
    potential_delay = max(
        0, round((earliest - departure).total_seconds() / 60)
    )
    return {
        "status": "possible" if risk else "no_indication",
        "risk": risk,
        "reason": (
            f"Nur {round(remaining)} Min. rechnerische Wendezeit bei "
            f"{min_turn} Min. angenommenem Mindestpuffer; "
            "mögliche Folgeverspätung, Fahrzeugdurchbindung unbestätigt"
            if risk else
            f"{round(remaining)} Min. rechnerische Wendezeit; "
            "keine Hinweise aus der möglichen Vorleistung"
        ),
        "incoming_planned": planned.isoformat(),
        "incoming_predicted": predicted.isoformat(),
        "incoming_delay_minutes": delay_minutes(incoming),
        "turnaround_buffer_minutes": round(remaining),
        "minimum_turnaround_minutes": min_turn,
        "earliest_plausible_outgoing": earliest.isoformat(),
        "estimated_minimum_followup_delay_minutes": potential_delay,
        "estimated_delay_only_if_same_vehicle": True,
        "confirmed_vehicle": False,
    }
