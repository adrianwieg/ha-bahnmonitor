"""DB timetable interface; no authentication is required by the public provider."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import API_URL
from .stations import known_station


class BahnApiError(Exception):
    """Remote provider failed."""


class StationNotFound(BahnApiError):
    """Station name was not found by the timetable API."""


class BahnApi:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            async with asyncio.timeout(18):
                async with self._session.get(
                    f"{API_URL}{path}", params=params,
                    headers={"User-Agent": "HomeAssistant-Bahnmonitor/0.1 (personal timetable monitor)"},
                ) as response:
                    if response.status == 429:
                        raise BahnApiError("Fahrplandienst begrenzt Anfragen (HTTP 429)")
                    response.raise_for_status()
                    return await response.json()
        except (TimeoutError, ClientError, ValueError) as exc:
            raise BahnApiError(str(exc)) from exc

    async def resolve_station(self, station: str) -> tuple[str, str]:
        known = known_station(station)
        if known is not None:
            return known
        results = await self._get("/locations", {"query": station, "results": 8, "poi": "false", "addresses": "false"})
        if not isinstance(results, list):
            raise BahnApiError("Invalid location response")
        candidates = [x for x in results if isinstance(x, dict) and x.get("id") and x.get("type") in ("stop", "station")]
        exact = next((x for x in candidates if x.get("name", "").casefold() == station.casefold()), None)
        chosen = exact or next((x for x in candidates if x.get("name", "").casefold().startswith(station.casefold())), None)
        if not chosen:
            raise StationNotFound(f"Bahnhof nicht gefunden: {station}")
        return str(chosen["id"]), str(chosen["name"])

    async def departures(self, station_id: str, when: datetime, minutes: int) -> list[dict]:
        data = await self._get(f"/stops/{station_id}/departures", {
            "when": when.isoformat(), "duration": minutes, "results": 80,
            "nationalExpress": "false", "national": "false", "regionalExpress": "true",
            "regional": "true", "suburban": "false", "bus": "false", "ferry": "false",
            "subway": "false", "tram": "false", "taxi": "false", "pretty": "false",
        })
        if isinstance(data, dict):
            data = data.get("departures", [])
        if not isinstance(data, list):
            raise BahnApiError("Invalid departure response")
        return data

    async def arrivals(self, station_id: str, when: datetime, minutes: int) -> list[dict]:
        data = await self._get(f"/stops/{station_id}/arrivals", {
            "when": when.isoformat(), "duration": minutes, "results": 100,
            "nationalExpress": "false", "national": "false", "regionalExpress": "true",
            "regional": "true", "suburban": "false", "bus": "false", "ferry": "false",
            "subway": "false", "tram": "false", "taxi": "false", "pretty": "false",
        })
        if isinstance(data, dict):
            data = data.get("arrivals", [])
        if not isinstance(data, list):
            raise BahnApiError("Invalid arrivals response")
        return data

    async def trip(self, trip_id: str) -> dict:
        from urllib.parse import quote
        data = await self._get(f"/trips/{quote(trip_id, safe='')}", {"stopovers": "true", "pretty": "false"})
        if not isinstance(data, dict):
            raise BahnApiError("Invalid trip response")
        return data
