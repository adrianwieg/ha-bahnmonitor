"""Report actual bus rides between explicitly selected bus stops.

These are independent from regular train clocks/IRIS station boards.
Public departure evidence does not automatically establish SEV classification:
'Bus RE1' may be shown as a bus, but only an explicit replacement-service
notice may turn the SEV binary sensor on.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from .dbf import norm
from .disruptions import classify


def _name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or (value.get("stop") or {}).get("name") or "")
    return str(value or "")


def _line(leg: dict) -> tuple[str, bool]:
    data = leg.get("line") or {}
    if not isinstance(data, dict):
        return "", False
    name = str(data.get("name") or data.get("id") or "")
    mode = str(data.get("mode") or "")
    product = str(data.get("product") or "")
    bus = mode.casefold() == "bus" or product.casefold() == "bus" or bool(
        re.search(r"\b(?:bus|ersatzbus)\b", name, re.I)
    )
    return name[:100], bus


def _stop_matches(actual: object, requested: str) -> bool:
    return bool(norm(_name(actual)) and norm(_name(actual)) == norm(requested))


def collect_bus_connections(
    response: object,
    *,
    direction: str,
    origin_stop: str,
    destination_stop: str,
    now: datetime,
) -> list[dict]:
    """Accept direct, individually timed bus legs between real bus stops."""
    if not isinstance(response, dict):
        return []
    result = {}
    for journey in (response.get("journeys") or [])[:16]:
        if not isinstance(journey, dict):
            continue
        legs = journey.get("legs") or []
        # Do not claim A→B direct buses from train+bus transfers.
        if not isinstance(legs, list) or len(legs) != 1:
            continue
        leg = legs[0]
        if not isinstance(leg, dict):
            continue
        line, bus = _line(leg)
        if not bus:
            continue
        if not (
            _stop_matches(leg.get("origin"), origin_stop)
            and _stop_matches(leg.get("destination"), destination_stop)
        ):
            continue
        departure = leg.get("plannedDeparture") or leg.get("departure")
        arrival = leg.get("plannedArrival") or leg.get("arrival")
        if not isinstance(departure, str) or not isinstance(arrival, str):
            continue
        try:
            scheduled = datetime.fromisoformat(departure.replace("Z", "+00:00"))
            end = datetime.fromisoformat(arrival.replace("Z", "+00:00"))
        except ValueError:
            continue
        if scheduled.tzinfo is None or end.tzinfo is None or end <= scheduled:
            continue
        if scheduled < now - timedelta(minutes=30):
            continue
        remarks = []
        for raw in (leg.get("remarks") or [])[:15]:
            if isinstance(raw, dict):
                text = raw.get("text") or raw.get("summary")
                if isinstance(text, str):
                    remarks.append(text[:220])
        explicit_sev = classify(line) == "sev" or any(classify(v) == "sev" for v in remarks)
        observed = {
            "direction": direction,
            "name": line or "Bus",
            "origin": origin_stop,
            "destination": destination_stop,
            "scheduled_departure": scheduled.isoformat(),
            "scheduled_arrival": end.isoformat(),
            "predicted_departure": leg.get("departure"),
            "predicted_arrival": leg.get("arrival"),
            "cancelled": bool(leg.get("cancelled")),
            "explicit_sev": explicit_sev,
            "remarks": remarks[:6],
            "source": "db.transport.rest (Bus-Haltestellen)",
            "timetable_evidence": True,
            "trip_id": str(leg.get("tripId") or "")[:120] or None,
        }
        key = (direction, line, scheduled.isoformat())
        result[key] = observed
    return sorted(result.values(), key=lambda row: row["scheduled_departure"])[:12]


def summarise_bus(
    rows: list[dict], *, source_status: str, errors: list[str] | None = None,
) -> dict:
    return {
        "status": source_status,
        "services": sorted(rows, key=lambda row: row["scheduled_departure"])[:20],
        "service_count": len(rows),
        "explicit_sev_count": sum(row["explicit_sev"] for row in rows),
        "errors": (errors or [])[:6],
        "note": (
            "Direkte Busfahrten zwischen den extra konfigurierten Bus-Haltestellen, "
            "unabhängig von den Zugzeiten. 'Bus RE1' allein ist keine "
            "amtlich bestätigte Schienenersatzverkehr-Meldung."
        ),
    }
