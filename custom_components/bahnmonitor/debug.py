"""Bounded, source-accurate debugging of route and replacement-bus discovery.

No extra API calls. All fields describe exactly what the upstream service
returned; never infer an SEV merely from an ordinary bus or empty timetable.
The snapshot is included in HA's downloadable integration diagnostics, not in
state attributes (which would exceed HA's state-size/recorder limits).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import re

from .dbf import clock_on_day, goes_to_destination, norm
from .disruptions import classify, _remarks
from .turnaround import passenger_line


def _name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or (value.get("stop") or {}).get("name")
    return " ".join(value.split())[:100] if isinstance(value, str) else ""


def _stops(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_name(item) for item in value[:15] if _name(item)]


def _messages(row: dict) -> list[str]:
    results = []
    messages = row.get("messages")
    if isinstance(messages, dict):
        for category in ("delay", "info", "qos"):
            results.extend(_remarks(messages.get(category)))
    results.extend(_remarks(row.get("remarks")))
    return list(dict.fromkeys(results))[:4]


def _string(value: object, limit: int = 120) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())[:limit]


def _line_tokens_match(text: str, filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    upper = text.upper()
    for requested in filters:
        m = re.fullmatch(r"([A-Z]{1,4})\s*(\d+[A-Z]?)", requested.strip().upper())
        if m:
            product, number = m.groups()
            if re.search(
                rf"(?<![A-Z0-9]){re.escape(product)}\s*{re.escape(number)}(?![A-Z0-9])",
                upper,
            ):
                return True
        elif requested.strip().upper() == upper.strip():
            return True
    return False


def board_probe(
    rows: object, *, now: datetime, station: str, other: str,
    direction: str, mode: str, lines: tuple[str, ...] = (),
    max_rows: int = 30,
) -> dict:
    """Explain exactly why each interesting row is / isn't part of the route.

    Capture a small sample of non-matching rows to distinguish empty provider
    responses from overly strict filters.
    """
    if not isinstance(rows, list):
        return {
            "status": "error", "station": station, "mode": mode,
            "direction": direction, "error": "Keine gültige Stationstafel",
            "entries": [],
        }
    total = len(rows)
    samples = []
    matching = 0
    route_matching = 0
    name_matching = 0
    sev_markers = 0
    bus_like = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        train = _name(row.get("train"))
        if not train:
            train = _string(row.get("train")) or ""
        line = _name(row.get("line")) or _string(row.get("line")) or ""
        destination = _name(row.get("destination"))
        origin = _name(row.get("origin"))
        messages = _messages(row)
        route = _stops(row.get("route"))
        via = _stops(row.get("via"))
        stop_names = [origin, destination, *route, *via]
        # A segment may be shorter than the requested relation. Debug this,
        # but DO NOT treat it as a confirmed match.
        contains_station = any(norm(v) == norm(other) for v in stop_names if v)
        if mode == "dep":
            matches_route = goes_to_destination(row, other)
        else:
            matches_route = (
                norm(origin) == norm(other)
                or norm(other) in [norm(v) for v in route[:-1]]
            )
        line_text = " ".join((train, line, *messages))
        filter_match = _line_tokens_match(line_text, lines)
        parsed_line = passenger_line(row, lines)
        is_bus = bool(re.search(r"\b(Bus|SEV|Ersatzbus)\b", line_text, re.I))
        explicit_sev = (
            classify(train) == "sev" or classify(line) == "sev"
            or any(classify(m) == "sev" for m in messages)
        )
        when_raw = row.get(
            "scheduledDeparture" if mode == "dep" else "scheduledArrival"
        )
        when = clock_on_day(when_raw, now)
        near = bool(
            when is not None
            and now - timedelta(minutes=70) <= when <= now + timedelta(minutes=180)
        )
        reasons = []
        if not matches_route:
            reasons.append("ziel_oder_streckenabschnitt_passt_nicht")
        if not filter_match:
            reasons.append("linienfilter_passt_nicht")
        if not parsed_line:
            reasons.append("keine_eindeutige_bahnlinie")
        if when is None:
            reasons.append("keine_sollzeit")
        elif not near:
            reasons.append("ausserhalb_zeitfenster")
        if matches_route:
            route_matching += 1
        if filter_match:
            name_matching += 1
        if matches_route and filter_match and parsed_line and near:
            matching += 1
        if explicit_sev:
            sev_markers += 1
        if is_bus:
            bus_like += 1
        candidate = {
            "station": station, "direction": direction, "mode": mode,
            "train": _string(train),
            "line": _string(line),
            "origin": origin or None,
            "destination": destination or None,
            "route": route,
            "via": via,
            "scheduled": _string(when_raw),
            "delay_minutes": row.get("delayDeparture" if mode == "dep" else "delayArrival"),
            "cancelled": row.get("isCancelled") is True,
            "is_bus_label": is_bus,
            "explicit_sev": explicit_sev,
            "mentions_opposite_endpoint": contains_station,
            "route_match": matches_route,
            "line_filter_match": filter_match,
            "passenger_line": parsed_line,
            "within_current_time_window": near,
            "excluded_by": reasons,
            "messages": messages,
        }
        # Give buses and explicitly reported SEV highest priority, whether
        # or not they match the monitored end stations.
        rank = (
            0 if explicit_sev else 1 if is_bus else
            2 if matches_route else 3 if filter_match else 4
        )
        samples.append((rank, candidate))
    samples.sort(key=lambda item: item[0])
    return {
        "status": "online", "station": station, "other": other,
        "direction": direction, "mode": mode,
        "raw_count": total,
        "route_match_count": route_matching,
        "line_filter_match_count": name_matching,
        "fully_matched_count": matching,
        "bus_label_count": bus_like,
        "explicit_sev_count": sev_markers,
        "line_filter": list(lines),
        "samples": [row for _, row in samples[:max_rows]],
        "sample_limit": max_rows,
        "truncated": len(samples) > max_rows,
    }


def journeys_probe(
    result: object, *, direction: str, requested_at: datetime,
    max_journeys: int = 6,
) -> dict:
    """Summarize real journey results, retaining the actual bus departure time."""
    if not isinstance(result, dict) or not isinstance(result.get("journeys"), list):
        return {"direction": direction, "status": "invalid_response",
                "requested_at": requested_at.isoformat(), "journeys": []}
    report = []
    for journey in result["journeys"][:max_journeys]:
        if not isinstance(journey, dict):
            continue
        legs = []
        for leg in (journey.get("legs") or [])[:12]:
            if not isinstance(leg, dict):
                continue
            line = leg.get("line") or {}
            if not isinstance(line, dict):
                line = {}
            name = _name(line.get("name"))
            messages = _remarks(leg.get("remarks"))
            label = f"{name} {line.get('product') or ''} {' '.join(messages)}"
            legs.append({
                "name": name or None,
                "product": _string(line.get("product")),
                "origin": _name(leg.get("origin")) or None,
                "destination": _name(leg.get("destination")) or None,
                "departure": _string(leg.get("departure")),
                "planned_departure": _string(leg.get("plannedDeparture")),
                "arrival": _string(leg.get("arrival")),
                "cancelled": bool(leg.get("cancelled")),
                "explicit_sev": classify(label) == "sev",
                "is_bus": bool(
                    line.get("product") == "bus" or
                    re.search(r"\bBus\b", name or "", re.I)
                ),
                "remarks": messages[:3],
            })
        report.append({
            "legs": legs,
            "remarks": _remarks(journey.get("remarks"))[:3],
        })
    return {
        "direction": direction, "status": "online",
        "requested_at": requested_at.isoformat(),
        "returned_journey_count": len(result["journeys"]),
        "journeys": report,
        "selection": (
            "Erste bis zu sechs Verbindungen ab Anfragezeitpunkt; keine "
            "vollständige Tagesliste. SEV kann zu anderen Zeiten fahren."
        ),
    }
