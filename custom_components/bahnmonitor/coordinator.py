"""Schedule-aware poller: full seven-day scan hourly, realtime nearer departure."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BahnApi, BahnApiError
from .logic import cancellation, delay_minutes, evaluate_turnaround, matches_departure, parse_time, select_incoming

_LOGGER = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Berlin")


class BahnCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, api: BahnApi, settings: dict, entry_id: str):
        super().__init__(hass, _LOGGER, name=f"bahnmonitor_{entry_id}", update_interval=timedelta(minutes=10))
        self.api = api
        self.settings = settings
        self._daily_cache: dict[str, dict] = {}
        self._last_full_check: datetime | None = None

    async def _async_update_data(self) -> dict:
        now = dt_util.now().astimezone(TZ)
        time_parts = [int(x) for x in self.settings["departure_time"].split(":")]
        weekdays = {int(x.strip()) for x in self.settings["weekdays"].split(",")}
        window = int(self.settings["window"])
        full = self._last_full_check is None or now - self._last_full_check >= timedelta(hours=1)
        results = []
        errors = []
        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            planned = datetime(day.year, day.month, day.day, time_parts[0], time_parts[1], tzinfo=TZ)
            if planned < now - timedelta(hours=2):
                continue
            key = day.isoformat()
            refresh = full or (offset <= 1 and planned < now + timedelta(hours=24)) or key not in self._daily_cache
            if refresh:
                try:
                    self._daily_cache[key] = await self._fetch_trip(planned, window, now)
                except BahnApiError as exc:
                    errors.append(str(exc))
                    if key not in self._daily_cache:
                        self._daily_cache[key] = {"date": key, "status": "unknown", "error": str(exc), "scheduled_departure": planned.isoformat()}
            results.append(self._daily_cache[key])
        self._daily_cache = {r["date"]: r for r in results}
        if full and not errors:
            self._last_full_check = now
        if errors and not any(r.get("status") != "unknown" for r in results):
            raise UpdateFailed("; ".join(errors))
        return {"journeys": results, "checked_at": now.isoformat(), "partial_errors": errors}

    async def _fetch_trip(self, planned: datetime, window: int, now: datetime) -> dict:
        settings = self.settings
        start = planned - timedelta(minutes=window)
        departures = await self.api.departures(settings["origin_id"], start, 2 * window + 2)
        candidates = [d for d in departures if matches_departure(d, settings["line"], planned, window)]
        checked = []
        for departure in sorted(candidates, key=lambda d: abs((parse_time(d.get("plannedWhen") or d.get("when")) - planned).total_seconds()))[:4]:
            trip_id = departure.get("tripId")
            if not trip_id:
                continue
            try:
                full_trip = await self.api.trip(trip_id)
            except BahnApiError:
                continue
            stopovers = full_trip.get("stopovers") or (full_trip.get("trip") or {}).get("stopovers") or []
            destination_idx = next((i for i, x in enumerate(stopovers) if str((x.get("stop") or {}).get("id")) == str(settings["destination_id"])), None)
            origin_idx = next((i for i, x in enumerate(stopovers) if str((x.get("stop") or {}).get("id")) == str(settings["origin_id"])), None)
            if destination_idx is None or origin_idx is None or origin_idx >= destination_idx:
                continue
            checked.append((departure, stopovers[destination_idx]))
        if not checked:
            return {"date": planned.date().isoformat(), "status": "not_found", "scheduled_departure": planned.isoformat(), "message": "Keine passende Direktfahrt gefunden; kein bestätigter Ausfall"}
        dep, last_stop = checked[0]
        effective_planned = parse_time(dep.get("plannedWhen") or dep.get("when")) or planned
        delay = delay_minutes(dep)
        canceled = cancellation(dep) or cancellation(last_stop) or bool(last_stop.get("arrivalCancelled"))
        status = "cancelled" if canceled else "delayed" if delay is not None and delay >= 3 else "on_time" if delay is not None else "scheduled"
        result = {
            "date": planned.date().isoformat(), "line": (dep.get("line") or {}).get("name"),
            "status": status, "scheduled_departure": effective_planned.isoformat(),
            "predicted_departure": dep.get("when"), "departure_delay_minutes": delay,
            "platform": dep.get("platform"), "scheduled_platform": dep.get("plannedPlatform"),
            "scheduled_arrival": last_stop.get("plannedArrival"), "predicted_arrival": last_stop.get("arrival"),
            "arrival_delay_minutes": round(last_stop.get("arrivalDelay") / 60) if isinstance(last_stop.get("arrivalDelay"), (int, float)) else None,
            "trip_id": dep.get("tripId"), "turnaround": {"status": "not_checked", "risk": False},
        }
        if settings.get("turnaround") and "göttingen" in settings["origin"].casefold() and planned <= now + timedelta(hours=18) and planned >= now - timedelta(hours=1):
            max_turn = int(settings["max_turn_minutes"])
            arrivals = await self.api.arrivals(settings["origin_id"], effective_planned - timedelta(minutes=max_turn), max_turn + 1)
            incoming = select_incoming(arrivals, settings["line"], effective_planned, max_turn)
            result["turnaround"] = evaluate_turnaround(incoming, effective_planned, int(settings["min_turn_minutes"]))
        return result
