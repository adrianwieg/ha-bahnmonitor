"""Human-friendly state and attributes for the Bahnmonitor entities."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
STATUS = {
    "on_time": "Pünktlich",
    "scheduled": "Planmäßig",
    "delayed": "Verspätet",
    "cancelled": "Fällt aus",
    "not_found": "Zug nicht gefunden",
    "unknown": "Keine Fahrtdaten",
}


def hhmm(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return stamp.astimezone(TZ).strftime("%H:%M") if stamp.tzinfo else stamp.strftime("%H:%M")


def format_journey(
    trip: dict | None,
    *,
    line: str,
    origin: str,
    destination: str,
) -> dict:
    """Return short strings for dashboards, preserving source status codes."""
    trip = trip or {}
    code = trip.get("status") or "unknown"
    stale = bool(trip.get("stale", True))
    exact = trip.get("line_match", "exact") == "exact"
    title = STATUS.get(code, "Unbekannt")
    if stale:
        title = (
            "Keine aktuellen Fahrtdaten"
            if code in ("unknown", "not_found") else "Daten veraltet"
        )
    elif not exact and code not in ("unknown", "not_found"):
        title = "Zuordnung unbestätigt"

    planned = hhmm(trip.get("scheduled_departure"))
    predicted = hhmm(trip.get("predicted_departure"))
    delay = trip.get("departure_delay_minutes")
    delay_str = (
        f"+{delay} Min" if isinstance(delay, (int, float)) and delay > 0
        else f"{delay} Min" if isinstance(delay, (int, float)) and delay < 0
        else "0 Min" if delay == 0 else "Unbekannt"
    )
    platform = trip.get("platform")
    scheduled_platform = trip.get("scheduled_platform")
    platform_changed = bool(
        platform and scheduled_platform and str(platform) != str(scheduled_platform)
    )
    platform_str = f"Gleis {platform}" if platform else "Gleis unbekannt"
    times = planned or "–"
    if predicted and predicted != planned:
        times += f" → {predicted}"
    summary = f"{line} · {origin} → {destination} · {times} · {title}"
    if delay is not None:
        summary += f" ({delay_str})"
    return {
        "display_status": title,
        "display_delay": delay_str,
        "display_departure": predicted or planned or "–",
        "display_planned": planned or "–",
        "display_platform": platform_str,
        "platform_changed": platform_changed,
        "display_summary": summary,
        "status_code": code,
        "is_stale": stale,
        "line_match": trip.get("line_match"),
    }
