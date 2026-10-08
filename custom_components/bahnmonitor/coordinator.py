"""Schedule-aware coordinator with provider outage protection and cached journeys."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import BahnApi, BahnApiError
from .logic import (
    cancellation, delay_minutes, evaluate_turnaround, matches_departure,
    parse_time, select_incoming,
)

_LOGGER = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Berlin")
BACKOFF_MINUTES = (15, 30, 60, 120)


class BahnCoordinator(DataUpdateCoordinator):
    """Keep the entry loaded even when the upstream timetable service fails."""

    def __init__(self, hass, api: BahnApi, settings: dict, entry_id: str):
        super().__init__(
            hass, _LOGGER, name=f"bahnmonitor_{entry_id}",
            update_interval=timedelta(minutes=10),
        )
        self.api = api
        self.settings = settings
        self._daily_cache: dict[str, dict] = {}
        self._last_full_check: datetime | None = None
        self._last_successful_update: datetime | None = None
        self._blocked_until: datetime | None = None
        self._failure_count = 0
        self._last_error: str | None = None

    async def _async_update_data(self) -> dict:
        now = dt_util.now().astimezone(TZ)
        hour, minute = (int(x) for x in self.settings["departure_time"].split(":")[:2])
        weekdays = {int(x.strip()) for x in self.settings["weekdays"].split(",")}
        window = int(self.settings["window"])
        full = self._last_full_check is None or now - self._last_full_check >= timedelta(hours=1)
        blocked = self._blocked_until is not None and now < self._blocked_until
        failed = False
        attempted = False
        journeys = []

        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            planned = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
            if planned < now - timedelta(hours=2):
                continue
            key = day.isoformat()
            cached = self._daily_cache.get(key)
            due = full or cached is None or (
                planned <= now + timedelta(hours=24)
                and planned >= now - timedelta(hours=2)
            )

            # A single 503 must not cause six additional failing requests
            # during the same update or during the provider backoff interval.
            if due and not blocked and not failed:
                attempted = True
                try:
                    cached = await self._fetch_trip(planned, window, now)
                except BahnApiError as exc:
                    failed = True
                    self._failure_count += 1
                    minutes = BACKOFF_MINUTES[min(self._failure_count - 1, len(BACKOFF_MINUTES) - 1)]
                    self._blocked_until = now + timedelta(minutes=minutes)
                    self._last_error = str(exc)
                    _LOGGER.warning(
                        "Bahnmonitor: Fahrplandienst nicht erreichbar: %s. "
                        "Nächster Versuch frühestens in %s Minuten",
                        exc, minutes,
                    )
                else:
                    self._failure_count = 0
                    self._blocked_until = None
                    self._last_error = None
                    self._last_successful_update = now
                    self._daily_cache[key] = cached

            if cached is None:
                cached = {
                    "date": key,
                    "status": "unknown",
                    "scheduled_departure": planned.isoformat(),
                    "message": "Fahrplandaten noch nicht verfügbar",
                }
                self._daily_cache[key] = cached

            # Never present a previously cached status as fresh during an outage.
            item = dict(cached)
            item["stale"] = bool(blocked or failed)
            journeys.append(item)

        self._daily_cache = {
            item["date"]: {k: v for k, v in item.items() if k != "stale"}
            for item in journeys
        }
        if full and attempted and not failed:
            self._last_full_check = now

        unavailable = failed or (
            self._blocked_until is not None and now < self._blocked_until
        )
        return {
            "journeys": journeys,
            "provider_status": "unavailable" if unavailable else (
                "online" if self._last_successful_update else "not_checked"
            ),
            "provider_error": self._last_error if unavailable else None,
            "retry_at": self._blocked_until.isoformat() if unavailable and self._blocked_until else None,
            "last_successful_update": self._last_successful_update.isoformat() if self._last_successful_update else None,
            "checked_at": now.isoformat(),
        }

    async def _fetch_trip(self, planned: datetime, window: int, now: datetime) -> dict:
        settings = self.settings
        start = planned - timedelta(minutes=window)
        departures = await self.api.departures(
            settings["origin_id"], start, 2 * window + 2
        )
        candidates = [
            d for d in departures
            if matches_departure(d, settings["line"], planned, window)
        ]
        checked = []
        for departure in sorted(
            candidates,
            key=lambda d: abs(
                (parse_time(d.get("plannedWhen") or d.get("when")) - planned).total_seconds()
            ),
        )[:4]:
            trip_id = departure.get("tripId")
            if not trip_id:
                continue

            # A transient error fetching the trip must not look like
            # "no matching train" or an implicitly cancelled service.
            full_trip = await self.api.trip(trip_id)
            stopovers = (
                full_trip.get("stopovers")
                or (full_trip.get("trip") or {}).get("stopovers")
                or []
            )
            destination_idx = next((
                i for i, stop in enumerate(stopovers)
                if str((stop.get("stop") or {}).get("id")) == str(settings["destination_id"])
            ), None)
            origin_idx = next((
                i for i, stop in enumerate(stopovers)
                if str((stop.get("stop") or {}).get("id")) == str(settings["origin_id"])
            ), None)
            if destination_idx is None or origin_idx is None or origin_idx >= destination_idx:
                continue
            checked.append((departure, stopovers[destination_idx]))

        if not checked:
            return {
                "date": planned.date().isoformat(),
                "status": "not_found",
                "scheduled_departure": planned.isoformat(),
                "message": "Keine passende Direktfahrt gefunden; kein bestätigter Ausfall",
            }

        dep, last_stop = checked[0]
        effective_planned = parse_time(dep.get("plannedWhen") or dep.get("when")) or planned
        delay = delay_minutes(dep)
        cancelled = (
            cancellation(dep)
            or cancellation(last_stop)
            or bool(last_stop.get("arrivalCancelled"))
        )
        status = (
            "cancelled" if cancelled else
            "delayed" if delay is not None and delay >= 3 else
            "on_time" if delay is not None else "scheduled"
        )
        result = {
            "date": planned.date().isoformat(),
            "line": (dep.get("line") or {}).get("name"),
            "status": status,
            "scheduled_departure": effective_planned.isoformat(),
            "predicted_departure": dep.get("when"),
            "departure_delay_minutes": delay,
            "platform": dep.get("platform"),
            "scheduled_platform": dep.get("plannedPlatform"),
            "scheduled_arrival": last_stop.get("plannedArrival"),
            "predicted_arrival": last_stop.get("arrival"),
            "arrival_delay_minutes": (
                round(last_stop["arrivalDelay"] / 60)
                if isinstance(last_stop.get("arrivalDelay"), (int, float)) else None
            ),
            "trip_id": dep.get("tripId"),
            "turnaround": {"status": "not_checked", "risk": False},
        }

        if (
            settings.get("turnaround")
            and "göttingen" in settings["origin"].casefold()
            and now - timedelta(hours=1) <= planned <= now + timedelta(hours=18)
        ):
            max_turn = int(settings["max_turn_minutes"])
            arrivals = await self.api.arrivals(
                settings["origin_id"],
                effective_planned - timedelta(minutes=max_turn),
                max_turn + 1,
            )
            incoming = select_incoming(
                arrivals, settings["line"], effective_planned, max_turn,
            )
            result["turnaround"] = evaluate_turnaround(
                incoming, effective_planned, int(settings["min_turn_minutes"]),
            )
        return result
