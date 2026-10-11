"""Route-level service alerts from actually returned connections.

The DB connection API can contain replacement-bus legs and service remarks
that are not exposed as train delay causes in IRIS station boards.
Do not infer a disruption from an ordinary bus transfer, or an all-clear from
the absence of a published message.
"""
from __future__ import annotations

import re

_SEV = re.compile(
    r"\bSEV\b|schienen[\s-]*ersatzverkehr|ersatzbus(?:verkehr)?|"
    r"ersatzverkehr|bus(?:se)? statt (?:bahn|zug|züge)|"
    r"rail replacement|replacement bus",
    re.IGNORECASE,
)
_CLOSURE = re.compile(r"strecken?\s*sperrung|vollsperrung|gesperrt|sperrung", re.IGNORECASE)
_CONSTRUCTION = re.compile(r"bauarbeiten|baustelle|bau(?:maß|mass)nahmen", re.IGNORECASE)
_CANCELLATION = re.compile(r"zugausfall|fällt aus|entfällt|ausfall", re.IGNORECASE)
_DIVERSION = re.compile(r"umleitung|abweichende[rn]? fahrweg", re.IGNORECASE)
_DELAY = re.compile(r"verspätung|verzögerung", re.IGNORECASE)

_RANK = {
    "sev": 0, "closure": 1, "cancellation": 2, "construction": 3,
    "diversion": 4, "delay": 5,
}


def _clean(value: object) -> str:
    return " ".join(value.split())[:300] if isinstance(value, str) else ""


def _remarks(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    result = []
    for remark in raw[:30]:
        if isinstance(remark, str):
            value = _clean(remark)
        elif isinstance(remark, dict):
            value = _clean(
                remark.get("text") or remark.get("summary")
                or remark.get("message")
            )
        else:
            continue
        if value:
            result.append(value)
    return result


def classify(message: str) -> str | None:
    """Accept explicit traffic language, not arbitrary transport remarks."""
    if _SEV.search(message):
        # A remark explicitly denying replacement service is not evidence.
        if re.search(r"\b(?:kein|keine|keinen|ohne)\s+(?:SEV|Schienenersatzverkehr|Ersatzverkehr)", message, re.I):
            return None
        return "sev"
    for kind, matcher in (
        ("closure", _CLOSURE), ("cancellation", _CANCELLATION),
        ("construction", _CONSTRUCTION), ("diversion", _DIVERSION),
        ("delay", _DELAY),
    ):
        if matcher.search(message):
            return kind
    return None


def _place(value: object) -> str | None:
    if isinstance(value, dict):
        return _clean(value.get("name")) or None
    return None


def extract_route_alerts(
    response: dict,
    *,
    direction: str,
    origin: str,
    destination: str,
) -> dict:
    """Extract evidenced messages from currently queried A-to-B journeys."""
    journeys = response.get("journeys")
    if not isinstance(journeys, list):
        raise ValueError("Missing journeys in route response")

    unique: dict[tuple[str, str, str], dict] = {}
    for journey in journeys[:12]:
        if not isinstance(journey, dict):
            continue

        def add(message: str, kind: str | None, leg: dict | None = None) -> None:
            if not kind:
                return
            line = ((leg or {}).get("line") or {})
            name = _clean(line.get("name")) if isinstance(line, dict) else ""
            key = (direction, kind, message.casefold())
            # A message may repeat across several train alternatives. Count it once
            # per direction; retain the first leg as supporting context.
            if key in unique:
                return
            unique[key] = {
                "type": kind, "text": message, "direction": direction,
                "route": f"{origin} → {destination}",
                "line": name or None,
                "from": _place((leg or {}).get("origin")),
                "to": _place((leg or {}).get("destination")),
                "source": "db.transport.rest",
            }

        for message in _remarks(journey.get("remarks")):
            add(message, classify(message))
        for leg in (journey.get("legs") or [])[:12]:
            if not isinstance(leg, dict):
                continue
            for message in _remarks(leg.get("remarks")):
                add(message, classify(message), leg)
            if any(leg.get(field) is True for field in (
                "cancelled", "departureCancelled", "arrivalCancelled",
            )):
                add("Ausfall eines Fahrtabschnitts in der Fahrplanauskunft gemeldet",
                    "cancellation", leg)
            line = leg.get("line") or {}
            name = _clean(line.get("name")) if isinstance(line, dict) else ""
            # An ordinary bus connection is never sufficient to infer SEV.
            if name and _SEV.search(name) and not (
                re.search(r"\bkein(?:e[nrms]?)?\s+", name, re.I)
            ):
                add(f"Ersatzverkehr in der Verbindung ausgewiesen ({name})", "sev", leg)

    incidents = sorted(unique.values(), key=lambda incident: (
        _RANK.get(incident["type"], 99), incident["direction"], incident["text"],
    ))
    return {
        "journey_count": len(journeys),
        "incidents": incidents[:20],
        "sev_reported": any(item["type"] == "sev" for item in incidents),
    }


def merge_route_alerts(route: dict, report: dict) -> dict:
    """Make published incidents prominent without manufacturing an all-clear."""
    merged = dict(route)
    incidents = report.get("incidents") or []
    source = report.get("source_status", "not_checked")
    merged.update(
        incidents=incidents,
        sev_reported=bool(report.get("sev_reported")),
        alert_source_status=source,
        alert_checked_at=report.get("checked_at"),
        alert_journey_count=report.get("journey_count", 0),
        alert_source_errors=report.get("errors", []),
        alert_scope="Aktuell abgefragte Verbindungen; kein vollständiges Baustellenregister",
    )
    if source not in ("online", "partial") or not incidents:
        if not incidents and source in ("online", "partial"):
            merged["alert_note"] = (
                "In den abgefragten Verbindungen keine ausdrückliche Störungsmeldung; "
                "dies ist keine Bestätigung einer störungsfreien Strecke."
            )
        elif source not in ("online", "partial"):
            merged["alert_note"] = "Verkehrsmeldungen nicht aktuell verfügbar; keine Entwarnung."
        return merged

    priority = incidents[0]["type"]
    labels = {
        "sev": ("sev", "Schienenersatzverkehr gemeldet"),
        "closure": ("closure", "Streckensperrung gemeldet"),
        "cancellation": ("high", "Zugausfall gemeldet"),
        "construction": ("construction", "Bauarbeiten gemeldet"),
        "diversion": ("diversion", "Umleitung gemeldet"),
        "delay": ("elevated", "Verspätungen gemeldet"),
    }
    code, label = labels[priority]
    # Cancellation and delay findings from IRIS can still be more severe than
    # generic construction/route notes. SEV and closures take precedence.
    if priority in ("sev", "closure") or merged.get("status_code") not in ("high",):
        merged["status_code"] = code
        merged["status"] = label
    texts = [item["text"] for item in incidents[:5]]
    merged["reason"] = "; ".join(texts + (
        [merged["reason"]] if merged.get("reason") and merged.get("sample_count") else []
    ))[:1600]
    merged["summary"] = (
        f"{len(incidents)} konkrete Verkehrsmeldung(en) in aktuell "
        f"abgefragten Verbindungen. " + merged.get("summary", "")
    )
    merged["trigger_reasons"] = texts + (merged.get("trigger_reasons") or [])
    return merged
