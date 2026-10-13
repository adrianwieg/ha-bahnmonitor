"""Opt-in Transitous MOTIS v6 route planning for noncommercial open-source use.

Usage policy: https://transitous.org/api/
Before enabling public MOTIS routing, project operators must contact the
Transitous maintainers about their expected routing load. This client does not
send ANY requests unless explicitly authorized in Home Assistant options.

Endpoint: https://api.transitous.org/api/v6/plan and /api/v1/geocode.
Not linked to, or dependent on, the DB transport.rest backend.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import re
from typing import Any

from aiohttp import ClientError

from .const import INTEGRATION_VERSION

API_BASE = "https://api.transitous.org"
ATTRIBUTION_URL = "https://transitous.org/sources/"
POLICY_URL = "https://transitous.org/api/"
USER_AGENT = (
    f"Bahnmonitor/{INTEGRATION_VERSION} "
    "(noncommercial open-source Home Assistant integration; "
    "+https://github.com/adrianwieg/ha-bahnmonitor)"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT = 12
MIN_REFRESH = timedelta(hours=2)
RETRY_AFTER_FAILURE = timedelta(hours=2)
_WALK = frozenset({"WALK", "FOOT", "PEDESTRIAN", "TRANSFER"})


class TransitousError(Exception):
    """An independent timetable source failed or had no safe stop match."""


def _norm(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(
        r"[^a-z0-9]", "",
        name.casefold().replace("ä", "ae").replace("ö", "oe")
        .replace("ü", "ue").replace("ß", "ss"),
    )


def _place_matches(name: str, candidate: str) -> bool:
    """Only an actual named station or its terminus, never a random suburb."""
    src, dst = _norm(name), _norm(candidate)
    if not src or not dst:
        return False
    # Preserve the configured full name, including Hbf suffix.
    if dst == src:
        return True
    for suffix in ("bahnhof", "hbf", "hauptbahnhof", "bahnhofzob", "zob"):
        if dst == src + suffix or dst == suffix + src:
            return True
    # "Göttingen, Bahnhof/ZOB" and "Leinefelde Bahnhof, Leinefelde-Worbis".
    parts = [v.strip() for v in candidate.split(",")]
    if len(parts) < 2 or not (
        "bahnhof" in dst or "zob" in dst or "hbf" in dst
    ):
        return False
    if any(_norm(p) == src for p in parts):
        return True
    return any(
        _norm(parts[0]) == src + suffix
        for suffix in ("bahnhof", "hbf", "hauptbahnhof", "zob")
    )


def select_stop(matches: Any, requested: str) -> dict | None:
    """Pick a unique stop in geocode results, never its position/ranking alone."""
    if not isinstance(matches, list):
        return None
    candidates: dict[str, dict] = {}
    for item in matches:
        if not isinstance(item, dict) or item.get("type") != "STOP":
            continue
        if not _place_matches(requested, str(item.get("name") or "")):
            continue
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        stop_id = item.get("id")
        if not isinstance(stop_id, str) or not stop_id:
            continue
        candidates[stop_id] = {
            "id": stop_id, "name": str(item["name"])[:150],
            "lat": lat, "lon": lon,
        }
    # Accept a unique exact station name preferentially over bus platforms.
    exact = [v for v in candidates.values()
             if _norm(v["name"]) == _norm(requested)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _iso(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return value.isoformat()
    except ValueError:
        return None


def _alert_texts(alerts: Any) -> list[str]:
    if not isinstance(alerts, list):
        return []
    values = []
    for alert in alerts[:8]:
        if not isinstance(alert, dict):
            continue
        parts = [alert.get("headerText"), alert.get("descriptionText")]
        text = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        if text:
            values.append(text[:400])
    return values[:5]


def _explicit_sev(label: str, alerts: list[str]) -> bool:
    haystack = " ".join((label, *alerts)).casefold()
    return bool(re.search(
        r"schienenersatzverkehr|schienen-ersatzverkehr|ersatzverkehr|"
        r"replacement bus|rail replacement|\bsev\b",
        haystack,
    ))


def normalize_motis(response: object, *, direction: str, now: datetime) -> dict:
    """Normalize only documented MOTIS itinerary / leg fields.

    A journey can start/end with a walk. Only one actual bus transit leg
    means a direct bus. Other multimodal itineraries stay separate.
    """
    if not isinstance(response, dict) or not isinstance(response.get("itineraries"), list):
        raise TransitousError("MOTIS: Antwort enthält keine itineraries-Liste")
    routes: list[dict] = []
    buses: dict[tuple[str, str, str], dict] = {}
    for itinerary in response["itineraries"][:20]:
        if not isinstance(itinerary, dict):
            continue
        legs = itinerary.get("legs")
        if not isinstance(legs, list):
            continue
        travel = []
        for leg in legs[:12]:
            if not isinstance(leg, dict):
                continue
            mode = str(leg.get("mode") or "").upper()
            if mode in _WALK or not mode:
                continue
            origin = leg.get("from") if isinstance(leg.get("from"), dict) else {}
            destination = leg.get("to") if isinstance(leg.get("to"), dict) else {}
            depart = _iso(leg.get("scheduledStartTime") or leg.get("startTime"))
            arrival = _iso(leg.get("scheduledEndTime") or leg.get("endTime"))
            if not depart or not arrival:
                continue
            try:
                if datetime.fromisoformat(arrival) <= datetime.fromisoformat(depart):
                    continue
            except ValueError:
                continue
            alerts = _alert_texts(leg.get("alerts"))
            label = (
                str(leg.get("displayName") or leg.get("routeShortName")
                    or leg.get("tripShortName") or mode).strip()
            )[:100]
            trip = {
                "mode": mode,
                "line": label,
                "origin": str(origin.get("name") or "")[:150],
                "destination": str(destination.get("name") or "")[:150],
                "scheduled_departure": depart,
                "scheduled_arrival": arrival,
                "predicted_departure": _iso(leg.get("startTime")),
                "predicted_arrival": _iso(leg.get("endTime")),
                "real_time": leg.get("realTime") is True,
                "cancelled": leg.get("cancelled") is True,
                "alerts": alerts,
                "explicit_sev": _explicit_sev(label, alerts),
            }
            travel.append(trip)
        if not travel:
            continue
        start = _iso(itinerary.get("startTime")) or travel[0]["scheduled_departure"]
        end = _iso(itinerary.get("endTime")) or travel[-1]["scheduled_arrival"]
        routes.append({
            "direction": direction, "source": "Transitous (MOTIS)",
            "scheduled_departure": travel[0]["scheduled_departure"],
            "scheduled_arrival": travel[-1]["scheduled_arrival"],
            "departure": start, "arrival": end,
            "transfers": itinerary.get("transfers"),
            "legs": travel[:8],
            "has_bus": any(t["mode"] == "BUS" for t in travel),
            "cancelled": any(t["cancelled"] for t in travel),
            "direct_bus": len(travel) == 1 and travel[0]["mode"] == "BUS",
        })
        if len(travel) != 1 or travel[0]["mode"] != "BUS":
            continue
        trip = travel[0]
        departure = datetime.fromisoformat(trip["scheduled_departure"])
        if departure < now - timedelta(minutes=30):
            continue
        item = {
            "direction": direction,
            "name": trip["line"] if trip["line"].lower().startswith("bus ") else f"Bus {trip['line']}",
            "origin": trip["origin"], "destination": trip["destination"],
            "scheduled_departure": trip["scheduled_departure"],
            "scheduled_arrival": trip["scheduled_arrival"],
            "predicted_departure": trip["predicted_departure"],
            "predicted_arrival": trip["predicted_arrival"],
            "cancelled": trip["cancelled"],
            "explicit_sev": trip["explicit_sev"],
            "remarks": trip["alerts"],
            "source": "Transitous (MOTIS)",
            "source_attribution": ATTRIBUTION_URL,
            "timetable_evidence": True,
            "real_time": trip["real_time"],
        }
        key = (direction, trip["line"], trip["scheduled_departure"])
        buses[key] = item
    return {
        "status": "online", "source": "Transitous (MOTIS)",
        "routes": routes[:16],
        "direct_buses": sorted(
            buses.values(), key=lambda row: row["scheduled_departure"]
        )[:12],
        "route_count": len(routes),
        "attribution_url": ATTRIBUTION_URL,
        "note": (
            "Öffentlicher GTFS-/Realtime-Fahrplan über Transitous. "
            "Die Buslinie allein beweist keinen offiziellen SEV. "
            "Eine leere Antwort bestätigt keinen Zugausfall."
        ),
    }


class TransitousClient:
    """Independent, consent-gated MOTIS API client with bounded requests."""

    def __init__(self, session):
        self.session = session

    async def _get(self, path: str, params: dict) -> object:
        try:
            async with asyncio.timeout(TIMEOUT):
                async with self.session.get(
                    API_BASE + path, params=params,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                ) as response:
                    if response.status != 200:
                        raise TransitousError(f"Transitous HTTP {response.status}")
                    body = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise TransitousError("Transitous: Antwort zu groß")
                    import json
                    return json.loads(body)
        except (ClientError, TimeoutError, ValueError) as exc:
            raise TransitousError(f"Transitous nicht erreichbar: {exc}") from exc

    async def geocode(self, station: str) -> dict:
        matches = await self._get("/api/v1/geocode", {
            "text": station, "type": "STOP", "numResults": 12,
            "language": "de",
        })
        match = select_stop(matches, station)
        if match is None:
            raise TransitousError(
                f"Transitous: '{station}' nicht eindeutig als Bahnhof gefunden"
            )
        return match

    async def plan(
        self, origin: dict, destination: dict, when: datetime,
        *, direction: str,
    ) -> dict:
        result = await self._get("/api/v6/plan", {
            # A station coordinate with radius links rail platforms to
            # neighboring ZOB/Busbahnhof without mixing up their IDs.
            "fromPlace": f"{origin['lat']},{origin['lon']}",
            "toPlace": f"{destination['lat']},{destination['lon']}",
            "time": when.isoformat(),
            "radius": 350,
            "maxTransfers": 1,
            "maxTravelTime": 180,
            "timetableView": "true",
            "searchWindow": 5400,
            "numItineraries": 8,
            "maxItineraries": 8,
            "detailedLegs": "false",
            "detailedTransfers": "false",
            "language": "de",
        })
        return normalize_motis(result, direction=direction, now=when)
