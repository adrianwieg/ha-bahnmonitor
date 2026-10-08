"""DB timetable interface; no authentication is required by the public provider."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

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
        # Shared by all configured Bahnmonitor entries.
        self._dbf_cache: dict[str, tuple[datetime, list[dict]]] = {}
        self._dbf_station_last: dict[str, datetime] = {}
        self._dbf_lock = asyncio.Lock()

    async def dbf_board(self, station_id: str, mode: str = "dep") -> list[dict]:
        """IRIS-TTS station board: near-term only, not seven-day data."""
        if mode not in ("dep", "arr"):
            raise ValueError("Invalid DBF board mode")
        now = datetime.now(timezone.utc)
        # Ask for arrival and departure data in one station board request.
        # DBF's documented "deparr" mode avoids a second IRIS request for
        # the same station, which the public service rate-limits.
        key = station_id
        async with self._dbf_lock:
            cached = self._dbf_cache.get(key)
            if cached and now - cached[0] < timedelta(seconds=75):
                return self._filter_board(cached[1], mode)
            last = self._dbf_station_last.get(station_id)
            if last and now - last < timedelta(seconds=65):
                raise BahnApiError("DBF station cooldown active (65 seconds)")
            self._dbf_station_last[station_id] = now
            try:
                async with asyncio.timeout(15):
                    async with self._session.get(
                        f"https://dbf.finalrewind.org/{quote(station_id, safe='')}.json",
                        params={"version": 3, "admode": "deparr", "limit": 100, "past": 1, "detailed": 1},
                        headers={"User-Agent": "Bahnmonitor/0.1 (+https://github.com/adrianwieg/ha-bahnmonitor)"},
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
            except (TimeoutError, ClientError, ValueError) as exc:
                raise BahnApiError(f"DBF/IRIS: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("departures"), list):
                error = data.get("error") if isinstance(data, dict) else None
                raise BahnApiError(f"DBF/IRIS: unexpected board response: {error or type(data).__name__}")
            entries = data["departures"]
            self._dbf_cache[key] = (datetime.now(timezone.utc), entries)
            return self._filter_board(entries, mode)

    @staticmethod
    def _filter_board(entries: list[dict], mode: str) -> list[dict]:
        key = "scheduledArrival" if mode == "arr" else "scheduledDeparture"
        return [
            entry for entry in entries
            if isinstance(entry, dict) and entry.get(key)
        ]

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            async with asyncio.timeout(18):
                async with self._session.get(
                    f"{API_URL}{path}", params=params,
                    headers={"User-Agent": "Bahnmonitor/0.1.3 (+https://github.com/adrianwieg/ha-bahnmonitor)"},
                ) as response:
                    if response.status == 429:
                        raise BahnApiError("Fahrplandienst begrenzt Anfragen (HTTP 429)")
                    if response.status in (502, 503, 504):
                        raise BahnApiError(f"Fahrplandienst vorübergehend nicht verfügbar (HTTP {response.status})")
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
