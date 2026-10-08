"""Daily GTFS regional-rail static timetable provider.

Source: GTFS Deutschland (NeTEx/DELFI), https://gtfs.de/de/feeds/de_rv/
Licensed CC BY 4.0. This provider supplies scheduled services ONLY, never
proof of operation or live cancellation, and is deliberately fetched once/day.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from zipfile import BadZipFile, ZipFile

from aiohttp import ClientError

LOGGER = logging.getLogger(__name__)
FEED_URL = "https://download.gtfs.de/germany/rv_free/latest.zip"
FEED_CREDIT = "GTFS Deutschland · DELFI e.V. · CC BY 4.0 · https://gtfs.de/"
TZ = ZoneInfo("Europe/Berlin")
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_UNCOMPRESSED = 700 * 1024 * 1024
RETRY_COOLDOWN = timedelta(hours=2)
REFRESH_INTERVAL = timedelta(hours=24)
MAX_CACHE_AGE = timedelta(hours=42)


class GtfsError(Exception):
    """Regional-rail feed could not be used safely."""


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold()
                  .replace("ö", "oe").replace("ä", "ae").replace("ü", "ue"))


def _station_match(name: str, configured: str) -> bool:
    # Avoid matching a suburb simply because it begins with Göttingen.
    if _norm(name) == _norm(configured):
        return True
    short = re.sub(
        r"[, ]+(hbf|hauptbahnhof|bahnhof)(?:/zob)?$",
        "", (name or "").strip(), flags=re.I,
    )
    return _norm(short) == _norm(configured)


def _read_csv(zf: ZipFile, filename: str):
    try:
        with zf.open(filename) as stream:
            with io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as reader:
                yield from csv.DictReader(reader)
    except KeyError as exc:
        raise GtfsError(f"GTFS enthält {filename} nicht") from exc


def _parse_gtfs(content: bytes) -> dict:
    """Stream the large stop_times table, keep only RE1/RE11 trains in memory."""
    try:
        with ZipFile(io.BytesIO(content)) as feed:
            if sum(item.file_size for item in feed.infolist()) > MAX_UNCOMPRESSED:
                raise GtfsError("GTFS-Archiv entpackt zu groß")
            routes = {
                row["route_id"]: re.sub(r"\s+", "", row.get("route_short_name") or "").upper()
                for row in _read_csv(feed, "routes.txt")
                if re.sub(r"\s+", "", row.get("route_short_name") or "").upper()
                in ("RE1", "RE11")
            }
            if not routes:
                raise GtfsError("RE 1/RE 11 fehlen in diesem GTFS-Feed")
            trips = {}
            for row in _read_csv(feed, "trips.txt"):
                route = routes.get(row.get("route_id"))
                if route:
                    trips[row["trip_id"]] = {
                        "line": "RE " + route[2:],
                        "service_id": row.get("service_id"),
                        "stops": [],
                    }
            stops = {}
            for row in _read_csv(feed, "stops.txt"):
                sid = row.get("stop_id")
                if sid:
                    stops[sid] = (row.get("stop_name") or "", row.get("parent_station") or "")
            for row in _read_csv(feed, "stop_times.txt"):
                trip = trips.get(row.get("trip_id"))
                if trip is not None:
                    try:
                        seq = int(row["stop_sequence"])
                    except (KeyError, ValueError):
                        continue
                    trip["stops"].append((
                        seq, row.get("stop_id"), row.get("departure_time"),
                        row.get("arrival_time"),
                    ))
            weekly = {}
            if "calendar.txt" in feed.namelist():
                for row in _read_csv(feed, "calendar.txt"):
                    weekly[row["service_id"]] = row
            exceptions = {}
            if "calendar_dates.txt" in feed.namelist():
                active_ids = {trip["service_id"] for trip in trips.values()}
                for row in _read_csv(feed, "calendar_dates.txt"):
                    if row.get("service_id") in active_ids:
                        exceptions[(row.get("service_id"), row.get("date"))] = row.get("exception_type")
            if not weekly and not exceptions:
                raise GtfsError("GTFS-Kalender nicht lesbar")
            return {
                "trips": trips, "stops": stops,
                "calendar": weekly, "exceptions": exceptions,
            }
    except (BadZipFile, OSError, UnicodeError, csv.Error) as exc:
        raise GtfsError(f"GTFS-Archiv beschädigt: {exc}") from exc


def _active(data: dict, service_id: str, target: date) -> bool:
    day = target.strftime("%Y%m%d")
    change = data["exceptions"].get((service_id, day))
    if change is not None:
        return change == "1"
    entry = data["calendar"].get(service_id)
    if not entry or not entry.get("start_date") or not entry.get("end_date"):
        return False
    return (
        entry["start_date"] <= day <= entry["end_date"]
        and entry.get(target.strftime("%A").lower()) == "1"
    )


def _gtfs_datetime(day: date, text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in text.split(":"))
        if not (0 <= minutes < 60 and 0 <= seconds < 60 and 0 <= hours <= 48):
            return None
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        return midnight + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    except (ValueError, TypeError):
        return None


def find_trip(
    data: dict, *, origin: str, destination: str,
    line: str, planned: datetime, tolerance: int,
) -> dict | None:
    """Only a unique, calendar-valid GTFS trip can confirm a scheduled service."""
    expected = re.sub(r"\s+", "", line).upper()
    candidates = []
    for trip in data["trips"].values():
        if re.sub(r"\s+", "", trip["line"]).upper() != expected:
            continue
        # GTFS times past midnight can belong to the previous service day.
        for service_day in (planned.date(), planned.date() - timedelta(days=1)):
            if not _active(data, trip["service_id"], service_day):
                continue
            origin_stop = None
            for seq, stop_id, dep_text, arr_text in sorted(trip["stops"]):
                name, parent = data["stops"].get(stop_id, ("", ""))
                p_name = data["stops"].get(parent, ("", ""))[0] if parent else ""
                if origin_stop is None:
                    if _station_match(name, origin) or _station_match(p_name, origin):
                        departure = _gtfs_datetime(service_day, dep_text)
                        if departure and abs((departure - planned).total_seconds()) <= tolerance * 60:
                            origin_stop = (seq, departure)
                    continue
                if seq <= origin_stop[0]:
                    continue
                if _station_match(name, destination) or _station_match(p_name, destination):
                    arrival = _gtfs_datetime(service_day, arr_text)
                    candidates.append((origin_stop[1], arrival, trip))
                    break
    if len(candidates) != 1:
        return None
    departure, arrival, trip = candidates[0]
    return {
        "date": planned.date().isoformat(),
        "status": "scheduled",
        "source": "GTFS Deutschland (Sollfahrplan)",
        "source_attribution": FEED_CREDIT,
        "line": trip["line"],
        "line_match": "exact",
        "scheduled_departure": departure.isoformat(),
        "scheduled_arrival": arrival.isoformat() if arrival else None,
        "predicted_departure": None,
        "predicted_arrival": None,
        "departure_delay_minutes": None,
        "arrival_delay_minutes": None,
        "platform": None,
        "trip_id": None,
        "realtime_confirmed": False,
        "turnaround": {"status": "not_checked", "risk": None, "confirmed_vehicle": False},
    }


class GtfsSchedule:
    def __init__(self, hass, session):
        self._hass = hass
        self._session = session
        self._data: dict | None = None
        self._fetched: datetime | None = None
        self._last_error: str | None = None
        self._next_retry: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def diagnostic(self) -> dict:
        return {
            "source": "GTFS Deutschland (regional rail)",
            "fetched_at": self._fetched.isoformat() if self._fetched else None,
            "last_error": self._last_error,
            "next_retry": self._next_retry.isoformat() if self._next_retry else None,
            "feed_loaded": self._data is not None,
        }

    async def _ensure(self):
        now = datetime.now(timezone.utc)
        if self._data is not None and self._fetched and now - self._fetched < REFRESH_INTERVAL:
            return
        if self._next_retry and now < self._next_retry:
            if self._data and self._fetched and now - self._fetched < MAX_CACHE_AGE:
                return
            raise GtfsError(self._last_error or "GTFS wartet auf Wiederholung")
        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._data is not None and self._fetched and now - self._fetched < REFRESH_INTERVAL:
                return
            if self._next_retry and now < self._next_retry:
                if self._data and self._fetched and now - self._fetched < MAX_CACHE_AGE:
                    return
                raise GtfsError(self._last_error or "GTFS wartet auf Wiederholung")
            try:
                async with asyncio.timeout(60):
                    async with self._session.get(
                        FEED_URL,
                        headers={"User-Agent": "Bahnmonitor/0.3.0 (+https://github.com/adrianwieg/ha-bahnmonitor)"},
                    ) as response:
                        response.raise_for_status()
                        chunks = []
                        size = 0
                        async for chunk in response.content.iter_chunked(131072):
                            size += len(chunk)
                            if size > MAX_DOWNLOAD:
                                raise GtfsError("GTFS-Download überschreitet 32 MiB")
                            chunks.append(chunk)
                parsed = await self._hass.async_add_executor_job(
                    _parse_gtfs, b"".join(chunks),
                )
            except (TimeoutError, ClientError, ValueError, GtfsError) as exc:
                self._last_error = str(exc)
                self._next_retry = now + RETRY_COOLDOWN
                LOGGER.warning("Bahnmonitor: GTFS-Sollfahrplan nicht verfügbar: %s", exc)
                if self._data and self._fetched and now - self._fetched < MAX_CACHE_AGE:
                    return
                raise GtfsError(self._last_error) from exc
            self._data = parsed
            self._fetched = datetime.now(timezone.utc)
            self._next_retry = None
            self._last_error = None
            LOGGER.info("Bahnmonitor: GTFS-Sollfahrplan geladen (RE 1 / RE 11)")

    async def find(self, settings: dict, when: datetime, tolerance: int) -> dict | None:
        await self._ensure()
        # Feed is shared across entries. Execute trips scan in HA's thread pool.
        result = await self._hass.async_add_executor_job(
            lambda: find_trip(
                self._data, origin=settings["origin"],
                destination=settings["destination"],
                line=settings["line"], planned=when, tolerance=tolerance,
            )
        )
        if result is not None and self._fetched is not None:
            result["feed_fetched_at"] = self._fetched.isoformat()
            result["stale"] = (
                datetime.now(timezone.utc) - self._fetched >= REFRESH_INTERVAL
            )
        return result
