"""Schedule-aware coordinator with provider outage protection and cached journeys."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import BahnApi, BahnApiError
from .gtfs import GtfsError
from .dbf import board_departure, incoming_candidates
from .route_health import collect_previous, collect_upcoming, observation_key, summarise
from .logic import (
    cancellation, delay_minutes, evaluate_turnaround, matches_departure,
    parse_time,
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
        self.gtfs = None  # Shared daily static regional rail timetable, if enabled.
        self._gtfs_error: str | None = None
        # Only the very first HA setup refresh is local. Slow downloads run
        # after entities are registered via a config-entry background task.
        self._startup_lightweight = False
        self.settings = settings
        self._daily_cache: dict[str, dict] = {}
        self._last_full_check: datetime | None = None
        self._last_successful_update: datetime | None = None
        self._blocked_until: datetime | None = None
        self._failure_count = 0
        self._last_error: str | None = None
        self._last_v6_attempt: datetime | None = None
        self._route_observations: dict[str, dict] = {}
        self._history_store = None
        self._dbf_debug: dict = {
            "status": "not_checked",
            "reason": "No departure in realtime window has been checked yet",
        }

    def _startup_snapshot(self, now: datetime) -> dict:
        """Provide usable HA entities without network I/O during setup."""
        hour, minute = (int(x) for x in self.settings["departure_time"].split(":")[:2])
        weekdays = {int(x.strip()) for x in self.settings["weekdays"].split(",")}
        journeys = []
        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            planned = datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=TZ,
            )
            if planned >= now - timedelta(hours=2):
                journeys.append({
                    "date": day.isoformat(),
                    "status": "unknown",
                    "scheduled_departure": planned.isoformat(),
                    "message": "Fahrplandaten werden im Hintergrund geladen",
                    "stale": True,
                })
        empty = {
            "count": 0, "delayed_count": 0, "cancelled_count": 0,
            "avg_delay_minutes": None, "trains": [],
            "current_departures": [], "awaiting_departures": [],
        }
        return {
            "journeys": journeys,
            "provider_status": "not_checked",
            "provider_error": None,
            "retry_at": None,
            "last_successful_update": None,
            "checked_at": now.isoformat(),
            "route_health": {
                "status_code": "loading",
                "status": "Wird geladen",
                "summary": "Streckenlage wird im Hintergrund aktualisiert.",
                "source_status": "loading",
                "sample_count": 0,
                "same_direction": dict(empty),
                "reverse_direction": dict(empty),
            },
            "diagnostics": {
                "setup_mode": "non_blocking_initial_snapshot",
                "future_timetable": {"status": "loading"},
                "gtfs_schedule": {"status": "loading"},
                "realtime_dbf": {"status": "not_checked"},
            },
        }

    async def _async_update_data(self) -> dict:
        now = dt_util.now().astimezone(TZ)
        if self._startup_lightweight:
            return self._startup_snapshot(now)
        hour, minute = (int(x) for x in self.settings["departure_time"].split(":")[:2])
        weekdays = {int(x.strip()) for x in self.settings["weekdays"].split(",")}
        window = int(self.settings["window"])
        full = self._last_full_check is None or now - self._last_full_check >= timedelta(hours=1)
        blocked = self._blocked_until is not None and now < self._blocked_until
        failed = False
        attempted = False
        gtfs_checked = False
        live_success = False
        journeys = []
        near_eligible = False
        next_planned = None

        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            planned = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
            if planned < now - timedelta(hours=2):
                continue
            key = day.isoformat()
            if next_planned is None:
                next_planned = planned
            cached = self._daily_cache.get(key)
            due = full or cached is None or (
                planned <= now + timedelta(hours=24)
                and planned >= now - timedelta(hours=2)
            )

            # A single 503 must not cause six additional failing requests
            # during the same update or during the provider backoff interval.
            fresh = False
            # DBF provides independent near-term IRIS-TTS predictions.
            near = -timedelta(minutes=30) <= planned - now <= timedelta(hours=4)
            if near:
                near_eligible = True
                self._dbf_debug = {
                    "status": "checking",
                    "station_id": str(self.settings.get("origin_id", "unknown")),
                    "line": self.settings["line"],
                    "planned_departure": planned.isoformat(),
                    "checked_at": now.isoformat(),
                }
                try:
                    dbf = await self._fetch_dbf(planned, window, now)
                except BahnApiError as exc:
                    self._dbf_debug.update(status="error", error=str(exc))
                    _LOGGER.warning("Bahnmonitor: DBF/IRIS timetable unavailable: %s", exc)
                else:
                    if dbf is not None:
                        self._dbf_debug.update(
                            status="matched", matched_source=dbf.get("source"),
                            line_match=dbf.get("line_match"),
                            observed_train=dbf.get("observed_train"),
                        )
                        cached = dbf
                        fresh = True
                        live_success = True
                        self._last_successful_update = now
                        self._daily_cache[key] = cached

            # An independent daily GTFS static feed supplies scheduled
            # trains for the next seven days; it NEVER proves realtime
            # operation, punctuality, cancellation or vehicle circulation.
            if due and not fresh and self.gtfs is not None:
                try:
                    gtfs_trip = await self.gtfs.find(self.settings, planned, window)
                except GtfsError as exc:
                    self._gtfs_error = str(exc)
                else:
                    gtfs_checked = True
                    self._gtfs_error = None
                    if gtfs_trip is not None:
                        cached = gtfs_trip
                        fresh = True
                        self._last_successful_update = now
                        self._daily_cache[key] = cached

            if due and not fresh and not blocked and not failed:
                attempted = True
                self._last_v6_attempt = now
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
                    fresh = True

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
            item["stale"] = bool(cached.get("stale") or ((blocked or failed) and not fresh))
            journeys.append(item)

        if not near_eligible:
            self._dbf_debug = {
                "status": "skipped",
                "reason": "outside_realtime_window",
                "window": "30 minutes before to 4 hours after current time",
                "next_scheduled_departure": next_planned.isoformat() if next_planned else None,
                "checked_at": now.isoformat(),
            }
        self._daily_cache = {
            item["date"]: {k: v for k, v in item.items() if k != "stale"}
            for item in journeys
        }
        # One successful GTFS scan already establishes the scheduled
        # 7-day picture. Avoid parsing all dates every ten minutes.
        if full and (gtfs_checked or (attempted and not failed)):
            self._last_full_check = now

        route_health = await self._route_health(next_planned, now)
        # A responsive corridor IRIS board is useful live data even if the
        # separate seven-day timetable provider is currently unavailable.
        if route_health.get("source_status") in ("online", "partial"):
            live_success = True
        unavailable = failed or (
            self._blocked_until is not None and now < self._blocked_until
        )
        provider_status = (
            "partial" if unavailable and live_success else
            "unavailable" if unavailable else
            "online" if self._last_successful_update else "not_checked"
        )
        _LOGGER.debug(
            "Bahnmonitor %s -> %s (%s, %s): %s, DBF=%s, next=%s, error=%s",
            self.settings.get("origin"), self.settings.get("destination"),
            self.settings.get("line"), self.settings.get("departure_time"),
            provider_status, self._dbf_debug.get("status"),
            next_planned.isoformat() if next_planned else None, self._last_error,
        )
        return {
            "journeys": journeys,
            "route_health": route_health,
            "provider_status": provider_status,
            "provider_error": self._last_error if unavailable else None,
            "retry_at": self._blocked_until.isoformat() if unavailable and self._blocked_until else None,
            "last_successful_update": self._last_successful_update.isoformat() if self._last_successful_update else None,
            "checked_at": now.isoformat(),
            "diagnostics": {
                "configured_line": self.settings["line"],
                "configured_route": f"{self.settings['origin']} -> {self.settings['destination']}",
                "configured_departure_time": self.settings["departure_time"],
                "configured_weekdays": self.settings["weekdays"],
                "next_scheduled_departure": next_planned.isoformat() if next_planned else None,
                "realtime_dbf": dict(self._dbf_debug),
                "gtfs_schedule": (
                    {**self.gtfs.diagnostic, "error": self._gtfs_error}
                    if self.gtfs is not None else {"status": "not_configured"}
                ),
                "future_timetable": {
                    "status": "backoff" if unavailable else "online" if attempted else "not_checked",
                    "last_attempt": self._last_v6_attempt.isoformat() if self._last_v6_attempt else None,
                    "last_error": self._last_error,
                    "retry_at": self._blocked_until.isoformat() if unavailable and self._blocked_until else None,
                },
            },
        }

    async def _route_health(self, target: datetime | None, now: datetime) -> dict:
        """Continuously watch RE 1 / RE 11 in both corridor directions.

        Queries are independent of the user's departure time, even if their
        journey is tomorrow. IRIS only reports a short station-board window;
        earlier observations are retained throughout the same day.
        """
        target_for_summary = target or now
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Never use yesterday's trains in today's corridor assessment.
        self._route_observations = {
            key: row for key, row in self._route_observations.items()
            if midnight.isoformat() <= row.get("scheduled_departure", "") < now.isoformat()
        }
        if not self.settings.get("history_enabled", True):
            data = summarise([], target=target_for_summary, now=now)
            data.update(
                status="Deaktiviert", status_code="disabled",
                summary="Die durchgehende Streckenüberwachung ist deaktiviert.",
                source_status="disabled",
            )
            data["source"] = "DBF/IRIS-TTS"
            return data

        errors: list[str] = []
        successful: list[str] = []
        upcoming: list[dict] = []
        observations_changed = False
        for direction, station_id, origin, destination in (
            ("same", self.settings["origin_id"],
             self.settings["origin"], self.settings["destination"]),
            ("reverse", self.settings["destination_id"],
             self.settings["destination"], self.settings["origin"]),
        ):
            try:
                board = await self.api.dbf_board(station_id, mode="dep")
            except BahnApiError as exc:
                errors.append(f"{direction}: {exc}")
                continue
            successful.append(direction)
            for row in collect_previous(
                board, direction=direction, origin=origin,
                destination=destination, planned=target_for_summary, now=now,
            ):
                key = observation_key(row)
                previous = self._route_observations.get(key)
                if previous is None or row["observed_at"] >= previous.get("observed_at", ""):
                    self._route_observations[key] = row
                    observations_changed = True
            current = collect_upcoming(
                board, direction=direction, origin=origin,
                destination=destination, now=now,
            )
            # A train's own future delay must never inflate the independent
            # corridor-risk assessment of that same train.
            if target is not None and target.date() == now.date():
                current = [
                    row for row in current
                    if not (
                        direction == "same"
                        and row["line"].replace(" ", "").upper()
                            == self.settings["line"].replace(" ", "").upper()
                        and abs(
                            (datetime.fromisoformat(row["scheduled_departure"]) - target).total_seconds()
                        ) <= int(self.settings["window"]) * 60
                    )
                    and datetime.fromisoformat(row["scheduled_departure"]) < target
                ]
            upcoming.extend(current)

        if observations_changed and self._history_store is not None:
            self._history_store.async_delay_save(
                lambda: {
                    "observations": list(self._route_observations.values()),
                },
                120,
            )

        data = summarise(
            list(self._route_observations.values()), target=target_for_summary,
            now=now, limit=3, upcoming=upcoming,
        )
        data["source_status"] = (
            "online" if len(successful) == 2
            else "partial" if successful
            else "unavailable"
        )
        if not successful:
            previous_status = data["status"]
            data["status"] = (
                "Daten veraltet" if data["sample_count"] > 0
                else "Datenquelle gestört"
            )
            data["status_code"] = (
                "stale" if data["sample_count"] > 0 else "source_unavailable"
            )
            data["last_known_assessment"] = previous_status
            data["summary"] = (
                "Die aktuellen Stationstafeln sind nicht erreichbar. "
                "Früher gesammelte Meldungen werden nicht als aktuelle "
                "Streckenbewertung verwendet."
            )
        elif len(successful) == 1:
            data["summary"] += " Nur eine Fahrtrichtung konnte aktualisiert werden."
        data["source"] = "DBF/IRIS-TTS"
        data["source_errors"] = errors[:2]
        data["observed_directions"] = successful
        data["monitoring_active"] = True
        data["monitoring_since"] = midnight.isoformat()
        data["next_departure"] = target.isoformat() if target else None
        data["own_train_realtime_starts_at"] = (
            (target - timedelta(hours=4)).isoformat() if target else None
        )
        data["collection_window"] = (
            "Laufende Beobachtung beider Richtungen am aktuellen Tag; "
            "IRIS liefert eine begrenzte Rückschau. "
            "Die Tageshistorie wird zwischen Aktualisierungen gespeichert."
        )
        _LOGGER.debug(
            "Bahnmonitor Streckenlage %s: %s, %s frühere / %s aktuelle "
            "Abfahrten, Quellen=%s",
            self.settings.get("name", self.settings["line"]),
            data["status"], data["sample_count"], data["current_count"],
            data["source_status"],
        )
        return data

    def _turnaround_choice(self) -> str:
        """Apply the GUI choice; keep old entries without an option compatible."""
        if "turnaround_at" in self.settings:
            value = self.settings["turnaround_at"]
            return value if value in ("origin", "destination", "off") else "off"
        if not self.settings.get("turnaround", True):
            return "off"
        return (
            "origin"
            if self.settings.get("origin", "").casefold() == "göttingen"
            else "off"
        )

    def _turnaround_state(self) -> dict:
        choice = self._turnaround_choice()
        selected = (
            self.settings["origin"] if choice == "origin"
            else self.settings["destination"] if choice == "destination"
            else None
        )
        return {
            "status": "not_checked",
            "risk": None,
            "turnaround_station": selected,
            "turnaround_choice": choice,
            "confirmed_vehicle": False,
        }

    async def _check_dbf_turnaround(
        self, departure: datetime, train_line: str,
    ) -> dict:
        info = self._turnaround_state()
        choice = info["turnaround_choice"]
        if choice == "off":
            return {**info, "status": "disabled", "reason": "Vorleistungsprüfung deaktiviert"}
        if choice == "destination":
            return {
                **info, "status": "not_applicable",
                "reason": (
                    "Der ausgewählte Wendebahnhof liegt am Ziel dieser Fahrt. "
                    "Sein ankommender Umlauf ist keine nachgewiesene Vorleistung "
                    "für die Abfahrt am Startbahnhof."
                ),
            }
        # Only a turn at the train's departure station can delay departure
        # through a possible incoming vehicle.
        try:
            arrivals = await self.api.dbf_board(self.settings["origin_id"], mode="arr")
        except BahnApiError as exc:
            return {
                **info, "status": "source_unavailable",
                "reason": f"Ankunftstafel nicht verfügbar: {exc}",
            }
        candidates = incoming_candidates(
            arrivals, train_line, departure,
            int(self.settings["max_turn_minutes"]),
        )
        if len(candidates) != 1:
            return {
                **info,
                "status": "ambiguous" if candidates else "unknown",
                "candidate_count": len(candidates),
                "reason": (
                    "Mehrere ankommende Fahrten derselben Linie: "
                    "Fahrzeugzuordnung nicht eindeutig"
                    if candidates else
                    "Keine passende ankommende Fahrt derselben Linie gefunden"
                ),
            }
        result = evaluate_turnaround(
            candidates[0], departure, int(self.settings["min_turn_minutes"]),
        )
        return {
            **info, **result, "candidate_count": 1,
            "observed_incoming_train": candidates[0].get("observed_train"),
            "confirmed_vehicle": False,
            "evidence": "same_line_plausible_turn_only",
        }

    async def _fetch_dbf(
        self, planned: datetime, window: int, now: datetime,
    ) -> dict | None:
        """Get near-term data from IRIS-TTS, without claiming seven-day coverage."""
        settings = self.settings
        board = await self.api.dbf_board(settings["origin_id"], mode="dep")
        self._dbf_debug.update(
            status="board_received",
            returned_count=len(board),
            sample=[
                {
                    "train": entry.get("train"),
                    "destination": entry.get("destination"),
                    "scheduledDeparture": entry.get("scheduledDeparture"),
                }
                for entry in board[:8] if isinstance(entry, dict)
            ],
        )
        result = board_departure(
            board, settings["line"], settings["destination"], planned, window,
        )
        if result is None:
            self._dbf_debug.update(
                status="no_matching_train",
                reason="No unambiguous configured line, destination, time match",
            )
            return None
        result["turnaround"] = await self._check_dbf_turnaround(
            planned, settings["line"],
        )
        return result

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
            "source": "db.transport.rest",
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
            "turnaround": self._turnaround_state(),
        }

        if now - timedelta(minutes=30) <= planned <= now + timedelta(hours=4):
            result["turnaround"] = await self._check_dbf_turnaround(
                effective_planned, settings["line"],
            )
        elif self._turnaround_choice() == "origin":
            result["turnaround"] = {
                **self._turnaround_state(),
                "status": "not_checked",
                "reason": "Vorleistungsprüfung frühestens vier Stunden vor Abfahrt",
            }
        elif self._turnaround_choice() == "destination":
            result["turnaround"] = {
                **self._turnaround_state(),
                "status": "not_applicable",
                "reason": "Wende am Zielbahnhof hat keinen bestätigten Einfluss auf diese Abfahrt",
            }
        else:
            result["turnaround"] = {
                **self._turnaround_state(),
                "status": "disabled",
                "reason": "Vorleistungsprüfung deaktiviert",
            }
        return result

