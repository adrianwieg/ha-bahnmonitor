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



def _board_station_matches(row: dict, name: str) -> bool:
    """Only connect messages to a station actually shown in the route."""
    def normalize(value: object) -> str:
        if isinstance(value, dict):
            value = value.get("name")
        if not isinstance(value, str):
            return ""
        clean = value.casefold().replace("ö", "oe").replace("ä", "ae").replace("ü", "ue")
        return re.sub(r"[^a-z0-9]", "", clean)

    target = normalize(name)
    if not target:
        return False
    if normalize(row.get("destination")) == target:
        return True
    route = row.get("route") if isinstance(row.get("route"), list) else []
    via = row.get("via") if isinstance(row.get("via"), list) else []
    return any(
        normalize(stop) == target
        for stop in route + via
        if isinstance(stop, (dict, str))
    )


def _board_line_matches(text: str, filters: tuple[str, ...]) -> bool:
    if not filters:
        return True
    for item in filters:
        match = re.fullmatch(r"([A-Z]{1,4})\s*(\d+[A-Z]?)", item.strip().upper())
        if match:
            product, number = match.groups()
            if re.search(
                rf"(?<![A-Z0-9]){re.escape(product)}\s*{re.escape(number)}(?![A-Z0-9])",
                text.upper(),
            ):
                return True
        elif item.strip().upper() == text.strip().upper():
            return True
    return False


def extract_board_alerts(
    board: list[dict],
    *,
    direction: str,
    origin: str,
    destination: str,
    lines: tuple[str, ...] = (),
) -> list[dict]:
    """Use only *explicit*, route-linked IRIS notices, not empty train boards.

    An ordinary bus, a message for a different destination, or the absence of
    running trains is NOT evidence of a replacement bus. A bus explicitly
    named 'Bus SEV' or a route train's published SEV remark is evidence.
    """
    events: dict[tuple[str, str, str], dict] = {}
    for row in board:
        if not isinstance(row, dict) or not _board_station_matches(row, destination):
            continue
        train = _clean(row.get("train"))
        line = _clean(row.get("line")) or train
        messages = row.get("messages")
        texts: list[str] = []
        if isinstance(messages, dict):
            for category in ("delay", "info", "qos"):
                texts.extend(_remarks(messages.get(category)))
        texts.extend(_remarks(row.get("remarks")))
        # A replacement bus might have no passenger-facing RE1 identifier:
        # with a selected line, do not attribute it to that line unproven.
        text_line = " ".join((train, line, *texts))
        if not _board_line_matches(text_line, lines):
            continue
        evidence: list[tuple[str, str]] = []
        if classify(train) == "sev" or classify(line) == "sev":
            evidence.append(("sev", f"Schienenersatzverkehr als Fahrt ausgewiesen ({train or line})"))
        for message in texts:
            kind = classify(message)
            if kind:
                evidence.append((kind, message))
        if row.get("isCancelled") is True:
            evidence.append(("cancellation", f"Zugausfall auf {origin} → {destination} gemeldet ({train})"))
        for kind, text in evidence:
            key = (direction, kind, text.casefold())
            events[key] = {
                "type": kind,
                "text": text,
                "direction": direction,
                "route": f"{origin} → {destination}",
                "line": line or None,
                "from": origin,
                "to": destination,
                "source": "DBF/IRIS-TTS",
            }
    return sorted(events.values(), key=lambda item: (
        _RANK.get(item["type"], 99), item["direction"], item["text"],
    ))[:20]


def merge_route_alerts(route: dict, report: dict) -> dict:
    """Combine independently observed IRIS notices with connection warnings.

    A confirmed event remains visible even when the connection API is down.
    A missing event with an unavailable provider is NEVER a negative result.
    """
    merged = dict(route)
    source = report.get("source_status", "not_checked")
    connection_events = report.get("incidents") or []
    board_events = route.get("board_incidents") or []
    events: dict[tuple[str, str, str, str], dict] = {}
    for incident in [*connection_events, *board_events]:
        if not isinstance(incident, dict):
            continue
        events[(
            str(incident.get("direction")),
            str(incident.get("type")),
            str(incident.get("source")),
            str(incident.get("text")).casefold(),
        )] = incident
    incidents = sorted(events.values(), key=lambda incident: (
        _RANK.get(incident.get("type"), 99),
        str(incident.get("direction")), str(incident.get("text")),
    ))[:20]

    merged.update(
        incidents=incidents,
        sev_reported=any(item.get("type") == "sev" for item in incidents),
        alert_source_status=source,  # The journey API status is not hidden.
        alert_checked_at=report.get("checked_at"),
        alert_journey_count=report.get("journey_count", 0),
        alert_source_errors=report.get("errors", []),
        alert_board_count=len(board_events),
        alert_sources={
            "connections": source,
            "station_boards": route.get("source_status", "not_checked"),
        },
        alert_scope=(
            "Aktuelle Verbindungen und ausdrücklich veröffentlichte IRIS-Meldungen; "
            "kein vollständiges Baustellenregister"
        ),
    )
    if not incidents:
        partial = (route.get("corridor") or {}).get("candidates") or []
        if partial and merged.get("status_code") in (
            "no_data", "limited", "partial_source_unavailable",
        ):
            # Board routes can reveal bus sections without explicit notices,
            # but they DO NOT establish that this is officially SEV.
            merged["status_code"] = "partial_bus_unconfirmed"
            merged["status"] = "Bus-Teilstrecke gefunden (SEV ungeprüft)"
            examples = ", ".join(dict.fromkeys(
                f"{item['train']} über {item['interchange']}"
                for item in partial[:3]
            ))
            merged["summary"] = (
                f"{len(partial)} mögliche Bus-Teilstrecke(n) mit Verknüpfung "
                f"zu einem weiteren Bahnabschnitt beobachtet: {examples}. "
                "Ein tatsächlicher Umstieg oder offizieller SEV ist damit "
                "nicht bestätigt."
            )
            merged["alert_note"] = (
                "Ersatzverkehr möglich, aber nicht bestätigt. "
                "Die Verbindungs-API ist "
                + ("erreichbar." if source == "online" else "nicht vollständig erreichbar.")
            )
            return merged
        if source != "online":
            merged["alert_note"] = (
                "Verbindungsdienst nicht vollständig erreichbar. "
                "Keine bestätigte Entwarnung; SEV und Ausfälle können fehlen."
            )
            if merged.get("status_code") in ("no_data", "limited"):
                merged["status_code"] = "partial_source_unavailable"
                merged["status"] = "Daten unvollständig"
                merged["summary"] = (
                    "Stationstafeln liefern derzeit keine ausreichenden "
                    "Streckeninformationen und der Verbindungsdienst ist "
                    "nicht vollständig erreichbar."
                )
        else:
            merged["alert_note"] = (
                "In den abgefragten Verbindungen und passenden Stationstafeln "
                "keine ausdrückliche Störungsmeldung gefunden; "
                "dies ist keine Bestätigung einer störungsfreien Strecke."
            )
        return merged

    if source not in ("online",):
        merged["alert_note"] = (
            "Bestätigte Einzelmeldungen aus den verfügbaren Quellen; "
            "der Verbindungsdienst ist nicht vollständig erreichbar. "
            "Weitere Störungen können fehlen."
        )
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
    if priority in ("sev", "closure") or merged.get("status_code") not in ("high",):
        merged["status_code"] = code
        merged["status"] = label
    texts = [item["text"] for item in incidents[:5]]
    merged["reason"] = "; ".join(texts + (
        [merged["reason"]] if merged.get("reason") and merged.get("sample_count") else []
    ))[:1600]
    merged["summary"] = (
        f"{len(incidents)} ausdrücklich veröffentlichte Verkehrsmeldung(en) "
        f"aus verfügbaren Quellen. " + merged.get("summary", "")
    )
    merged["trigger_reasons"] = texts + (merged.get("trigger_reasons") or [])
    return merged
