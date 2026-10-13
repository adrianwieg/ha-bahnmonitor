"""Schedule-aware coordinator with provider outage protection and cached journeys."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import BahnApi, BahnApiError
from .gtfs import GtfsError
from .dbf import board_departure, incoming_candidates, match_inbound_origin
from .disruptions import extract_route_alerts, extract_board_alerts, merge_route_alerts
from .debug import board_probe, journeys_probe
from .turnaround import detect_turnarounds
from .route_departures import collect_departures, collect_arrivals, summarise_departures
from .corridor import find_partial_bus_sections
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
        self._alerts_checked_at: datetime | None = None
        self._alerts_retry_at: datetime | None = None
        self._alerts_cache: dict | None = None
        self._debug_last_probe_at: datetime | None = None
        self._source_debug: dict = {"station_boards": {}, "journey_queries": {}, "note": "Noch keine Live-Abfrage"}
        self._entry_id = entry_id
        self._station_retry_at: datetime | None = None
        self._station_lookup_error: str | None = None
        self._dbf_debug: dict = {
            "status": "not_checked",
            "reason": "No departure in realtime window has been checked yet",
        }

    def _startup_snapshot(self, now: datetime) -> dict:
        """Provide usable HA entities without network I/O during setup."""
        if self.settings.get("monitor_mode", "train") == "route":
            return {
                "journeys": [],
                "provider_status": "not_checked",
                "checked_at": now.isoformat(),
                "route_health": {
                    "status_code": "loading", "status": "Wird geladen",
                    "summary": "Strecke und Verkehrsmeldungen werden geprüft.",
                    "source_status": "loading", "alert_source_status": "loading",
                    "incidents": [], "sev_reported": False,
                    "sample_count": 0,
                },
                "diagnostics": {"setup_mode": "non_blocking_route_startup"},
            }
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

    def _station_waiting_snapshot(self, now: datetime) -> dict:
        """A temporary location lookup outage must not crash the integration."""
        info = self._station_lookup_error or "Bahnhofsdaten werden automatisch nachgeladen."
        return {
            "journeys": [],
            "provider_status": "unavailable",
            "provider_error": info,
            "checked_at": now.isoformat(),
            "retry_at": self._station_retry_at.isoformat() if self._station_retry_at else None,
            "route_health": {
                "status": "Bahnhofsdaten fehlen",
                "status_code": "station_lookup_pending",
                "summary": (
                    f"{self.settings.get('origin')} → {self.settings.get('destination')}: "
                    "Bahnhofs-IDs fehlen; die Bahnhofssuche wird automatisch wiederholt. "
                    "Solange sind keine belastbaren Streckenmeldungen verfügbar."
                ),
                "source_status": "unavailable",
                "alert_source_status": "unavailable",
                "sev_reported": False,
                "incidents": [],
                "sample_count": 0,
                "timetable": {"source_status": "unavailable", "departures": [], "arrivals": []},
                "turnaround": {"status": "source_unavailable", "source_status": "unavailable",
                                "candidates": []},
                "direction_labels": {
                    "same": f"{self.settings.get('origin')} → {self.settings.get('destination')}",
                    "reverse": f"{self.settings.get('destination')} → {self.settings.get('origin')}",
                },
            },
            "diagnostics": {
                "station_lookup": {
                    "status": "pending",
                    "error": info,
                    "retry_at": self._station_retry_at.isoformat() if self._station_retry_at else None,
                    "origin_id": self.settings.get("origin_id") or None,
                    "destination_id": self.settings.get("destination_id") or None,
                },
            },
        }

    async def _ensure_station_ids(self, now: datetime) -> bool:
        """Retry missing EVA IDs without failing setup or spamming the provider."""
        if self.settings.get("origin_id") and self.settings.get("destination_id"):
            return True
        if self._station_retry_at and now < self._station_retry_at:
            return False
        errors = []
        for key in ("origin", "destination"):
            if self.settings.get(f"{key}_id"):
                continue
            try:
                station_id, station_name = await self.api.resolve_station(
                    self.settings[key]
                )
            except BahnApiError as exc:
                errors.append(f"{self.settings[key]}: {exc}")
            else:
                self.settings[f"{key}_id"] = station_id
                self.settings[key] = station_name
        if (
            self.settings.get("origin_id")
            and self.settings.get("destination_id")
            and self.settings["origin_id"] == self.settings["destination_id"]
        ):
            errors.append("Start und Ziel haben dieselbe Bahnhof-ID")
            self.settings["destination_id"] = ""
        if errors or not (
            self.settings.get("origin_id") and self.settings.get("destination_id")
        ):
            self._station_lookup_error = "; ".join(errors) or "Bahnhofssuche nicht verfügbar"
            self._station_retry_at = now + timedelta(minutes=15)
            return False

        self._station_lookup_error = None
        self._station_retry_at = None
        self.settings["station_lookup_pending"] = False
        _LOGGER.info(
            "Bahnmonitor: Bahnhofsdaten erfolgreich nachgeladen: %s → %s",
            self.settings["origin"], self.settings["destination"],
        )
        # Save recovered IDs to prevent another lookup on every HA restart.
        # Guard for isolated test stubs with hass=None.
        hass = getattr(self, "hass", None)
        if hass is not None:
            entry = hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data,
                                  "origin": self.settings["origin"],
                                  "origin_id": self.settings["origin_id"],
                                  "destination": self.settings["destination"],
                                  "destination_id": self.settings["destination_id"],
                                  "station_lookup_pending": False},
                )
        return True

    async def _async_update_data(self) -> dict:
        now = dt_util.now().astimezone(TZ)
        if self._startup_lightweight:
            return self._startup_snapshot(now)
        if not await self._ensure_station_ids(now):
            return self._station_waiting_snapshot(now)
        if self.settings.get("monitor_mode", "train") == "route":
            return await self._async_update_route_only(now)
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
                    if hasattr(self.gtfs, "find_explained"):
                        gtfs_trip, gtfs_detail = await self.gtfs.find_explained(
                            self.settings, planned, window,
                        )
                    else:
                        # Compatibility with a simple alternative timetable
                        # provider implementing the original find contract.
                        gtfs_trip = await self.gtfs.find(
                            self.settings, planned, window,
                        )
                        gtfs_detail = {}
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
                    elif gtfs_detail:
                        reason = gtfs_detail.get("reason")
                        message = {
                            "no_matching_direct_route":
                                "Im GTFS-Sollfahrplan keine direkte RE-Verbindung "
                                "zwischen Start und Ziel gefunden.",
                            "no_departure_within_search_window":
                                "GTFS enthält die Strecke, aber keine passende "
                                "Abfahrt im konfigurierten Zeitfenster.",
                            "ambiguous_multiple_departures":
                                "GTFS enthält mehrere passende Abfahrten; "
                                "Zuordnung nicht eindeutig.",
                            "no_active_calendar_service":
                                "Kein passender Kalendereintrag im GTFS-Feed.",
                            "line_not_in_feed":
                                "Die konfigurierte Linie fehlt im GTFS-Feed.",
                        }.get(reason, "Keine bestätigte GTFS-Fahrt gefunden.")
                        nearest = gtfs_detail.get("nearest_planned_departures") or []
                        origin_services = gtfs_detail.get("nearest_origin_departures") or []
                        if nearest and reason == "no_departure_within_search_window":
                            message += f" Nächste bekannte Sollabfahrt: {nearest[0][11:16]}."
                        if reason == "no_matching_direct_route" and origin_services:
                            candidate = origin_services[0]
                            end = candidate.get("last_stop") or "unbekannt"
                            time = str(candidate.get("time") or "")[11:16]
                            message += (
                                f" RE-Abfahrt am Startbahnhof im Feed: "
                                f"{time} Uhr, letzter Halt {end}."
                            )
                        cached = {
                            "date": key,
                            "status": "unknown",
                            "scheduled_departure": planned.isoformat(),
                            "message": message,
                            "gtfs_match_reason": reason,
                            "gtfs_nearest": nearest,
                            "gtfs_origin_services": gtfs_detail.get(
                                "nearest_origin_departures", [],
                            ),
                            "source_checked": "GTFS Deutschland (Sollfahrplan)",
                            "timetable_confirmed": False,
                        }
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
        if self._turnaround_choice() == "auto":
            self._attach_automatic_turnarounds(journeys, route_health)
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

    async def _async_update_route_only(self, now: datetime) -> dict:
        """Monitor any configured A-to-B route without an individual train time."""
        route = await self._route_health(None, now)
        board_ok = route.get("source_status") in ("online", "partial")
        alerts_ok = route.get("alert_source_status") in ("online", "partial")
        provider_status = (
            "online" if board_ok and alerts_ok
            else "partial" if board_ok or alerts_ok
            else "unavailable"
        )
        return {
            "journeys": [], "route_health": route,
            "provider_status": provider_status,
            "provider_error": "; ".join(route.get("source_errors", []) + route.get("alert_source_errors", [])) or None,
            "checked_at": now.isoformat(),
            "last_successful_update": now.isoformat() if board_ok or alerts_ok else None,
            "retry_at": self._alerts_retry_at.isoformat() if self._alerts_retry_at else None,
            "diagnostics": {
                "monitor_mode": "route",
                "configured_route": f"{self.settings['origin']} → {self.settings['destination']}",
                "line_filter": self.settings.get("line") or None,
                "connection_alerts": {
                    "source_status": route.get("alert_source_status"),
                    "checked_at": route.get("alert_checked_at"),
                    "journey_count": route.get("alert_journey_count"),
                    "errors": route.get("alert_source_errors", []),
                },
                "station_boards": route.get("board_counts", {}),
                "partial_corridor": {
                    "status": (route.get("corridor") or {}).get("status"),
                    "candidate_count": (route.get("corridor") or {}).get("candidate_count", 0),
                },
                "debug_snapshot_available": bool(self._source_debug.get("station_boards")),
                "route_probe_summary": {
                    key: {
                        "raw_count": value.get("raw_count", 0),
                        "route_match_count": value.get("route_match_count", 0),
                        "line_filter_match_count": value.get("line_filter_match_count", 0),
                        "bus_label_count": value.get("bus_label_count", 0),
                        "explicit_sev_count": value.get("explicit_sev_count", 0),
                        "status": value.get("status"),
                    }
                    for key, value in self._source_debug.get("station_boards", {}).items()
                },
                "iris_route_alert_count": route.get("alert_board_count", 0),
                "alert_sources": route.get("alert_sources", {}),
                "turnaround_relevant_services": (
                    (route.get("turnaround") or {}).get("relevant_services", 0)
                ),
            },
        }

    async def async_debug_connection_at(self, requested_at: datetime) -> dict:
        """Manually probe connections at a different clock time.

        Does NOT mark any train/SEV as confirmed or change the live sensors.
        Called exclusively by a user-triggered HA action, not every refresh.
        """
        now = dt_util.now().astimezone(TZ)
        if (
            self._debug_last_probe_at is not None
            and now < self._debug_last_probe_at + timedelta(seconds=60)
        ):
            return {
                "status": "rate_limited",
                "retry_at": (
                    self._debug_last_probe_at + timedelta(seconds=60)
                ).isoformat(),
                "note": "Debugabfrage erst nach 60 Sekunden wiederholen.",
            }
        self._debug_last_probe_at = now
        rows = {}
        for direction, start_id, end_id, origin, destination in (
            ("same", self.settings.get("origin_id"), self.settings.get("destination_id"),
             self.settings.get("origin"), self.settings.get("destination")),
            ("reverse", self.settings.get("destination_id"), self.settings.get("origin_id"),
             self.settings.get("destination"), self.settings.get("origin")),
        ):
            if not start_id or not end_id:
                rows[direction] = {
                    "status": "error", "reason": "Bahnhof-IDs fehlen",
                    "origin": origin, "destination": destination,
                }
                continue
            request = getattr(self.api, "journeys", None)
            if request is None:
                rows[direction] = {"status": "unsupported"}
                continue
            try:
                raw = await request(start_id, end_id, requested_at)
                rows[direction] = {
                    **journeys_probe(raw, direction=direction, requested_at=requested_at),
                    "origin": origin, "destination": destination,
                }
            except (BahnApiError, ValueError) as exc:
                rows[direction] = {
                    "status": "error", "error": str(exc),
                    "requested_at": requested_at.isoformat(),
                    "origin": origin, "destination": destination,
                }
        record = {
            "requested_at": requested_at.isoformat(), "directions": rows,
            "note": (
                "Gezielte Testabfrage zu gewählter Uhrzeit. Busse werden unabhängig "
                "von den RE-Fahrzeiten gezeigt. Ein gewöhnlicher Bus beweist keinen SEV."
            ),
        }
        self._source_debug.setdefault("manual_probes", []).append(record)
        self._source_debug["manual_probes"] = self._source_debug["manual_probes"][-3:]
        _LOGGER.info(
            "Bahnmonitor Debugabfrage %s um %s: %s",
            self.settings.get("name"), requested_at.isoformat(),
            ", ".join(f"{key}={value.get('status')}" for key, value in rows.items()),
        )
        return record

    async def _route_alerts(self, now: datetime) -> dict:
        """Poll route connections sparingly; failures must not be a false all-clear."""
        request = getattr(self.api, "journeys", None)
        if request is None:
            self._source_debug["journey_queries"] = {
                "all": {"status": "unsupported", "reason": "Keine Verbindungsabfrage verfügbar"}
            }
            return {"source_status": "unsupported", "incidents": [], "errors": []}
        if self._alerts_checked_at and self._alerts_cache and (
            now - self._alerts_checked_at < timedelta(minutes=30)
        ):
            self._source_debug["journey_cache"] = {
                "status": "cached", "last_checked": self._alerts_checked_at.isoformat(),
                "next_refresh": (self._alerts_checked_at + timedelta(minutes=30)).isoformat(),
            }
            return self._alerts_cache
        if self._alerts_retry_at and now < self._alerts_retry_at:
            self._source_debug["journey_cache"] = {
                "status": "backoff",
                "next_retry": self._alerts_retry_at.isoformat(),
            }
            return {
                "source_status": "unavailable", "incidents": [],
                "errors": ["Fahrplandienst: Wiederholungsversuch ausstehend"],
            }

        success = 0
        failures = []
        incidents = []
        journey_count = 0
        self._source_debug["journey_queries"] = {}
        self._source_debug["journey_cache"] = {"status": "live_query"}
        for direction, origin_id, destination_id, origin, destination in (
            ("same", self.settings["origin_id"], self.settings["destination_id"],
             self.settings["origin"], self.settings["destination"]),
            ("reverse", self.settings["destination_id"], self.settings["origin_id"],
             self.settings["destination"], self.settings["origin"]),
        ):
            try:
                response = await request(origin_id, destination_id, now)
                parsed = extract_route_alerts(
                    response, direction=direction,
                    origin=origin, destination=destination,
                )
            except (BahnApiError, ValueError) as exc:
                failures.append(f"{direction}: {exc}")
                self._source_debug["journey_queries"][direction] = {
                    "status": "error", "requested_at": now.isoformat(),
                    "error": str(exc), "origin": origin, "destination": destination,
                    "query_scope": "Verbindungen ab Jetzt; keine Tagesgesamtübersicht",
                }
                continue
            self._source_debug["journey_queries"][direction] = {
                **journeys_probe(response, direction=direction, requested_at=now),
                "origin": origin, "destination": destination,
            }
            success += 1
            journey_count += parsed["journey_count"]
            incidents.extend(parsed["incidents"])

        result = {
            "source_status": (
                "online" if success == 2 else
                "partial" if success else "unavailable"
            ),
            "incidents": sorted(
                incidents, key=lambda item: (
                    {"sev": 0, "closure": 1, "cancellation": 2,
                     "construction": 3, "diversion": 4, "delay": 5}.get(item["type"], 9),
                    item["direction"], item["text"],
                ),
            )[:20] if success else [],
            "journey_count": journey_count,
            "sev_reported": any(x["type"] == "sev" for x in incidents) if success else False,
            "checked_at": now.isoformat() if success else None,
            "errors": failures,
        }
        if success:
            self._alerts_cache = result
            self._alerts_checked_at = now
            self._alerts_retry_at = None
        else:
            self._alerts_cache = None
            self._alerts_checked_at = None
            self._alerts_retry_at = now + timedelta(minutes=30)
        return result

    async def _with_alerts(self, route: dict, now: datetime) -> dict:
        """Overlay verified connection notices on independent IRIS observations."""
        return merge_route_alerts(route, await self._route_alerts(now))

    def _attach_automatic_turnarounds(self, journeys: list[dict], route_health: dict) -> None:
        """Only associate an inferred return trip with the matching departure."""
        candidates = (route_health.get("turnaround") or {}).get("candidates", [])
        for journey in journeys:
            planned = parse_time(journey.get("scheduled_departure"))
            if planned is None:
                continue
            relevant = [
                candidate for candidate in candidates
                if candidate["station"].casefold() == self.settings["origin"].casefold()
                and abs((datetime.fromisoformat(candidate["departure_scheduled"]) - planned).total_seconds())
                    <= int(self.settings.get("window", 20)) * 60
                and (
                    not self.settings.get("line")
                    or candidate["line"].replace(" ", "").upper()
                        == self.settings["line"].replace(" ", "").upper()
                )
            ]
            if len(relevant) == 1:
                candidate = relevant[0]
                journey["turnaround"] = {
                    "status": "possible" if candidate["status"] == "plausible" else "ambiguous",
                    "risk": candidate["possible_followup_delay"] if candidate["status"] == "plausible" else None,
                    "reason": "Automatisch vermutete Zugwende; Fahrzeugzuordnung unbestätigt.",
                    "turnaround_station": candidate["station"],
                    "turnaround_choice": "auto",
                    "incoming_planned": candidate["arrival_scheduled"],
                    "incoming_predicted": candidate["arrival_predicted"],
                    "outgoing_planned": candidate["departure_scheduled"],
                    "incoming_delay_minutes": candidate["incoming_delay_minutes"],
                    "turnaround_buffer_minutes": candidate["predicted_turn_minutes"],
                    "minimum_turnaround_minutes": candidate["minimum_turn_minutes"],
                    "estimated_minimum_followup_delay_minutes": candidate["potential_delay_minutes_if_same_vehicle"],
                    "estimated_delay_only_if_same_vehicle": True,
                    "confirmed_vehicle": False,
                    "evidence": candidate["confidence"],
                }
            elif len(relevant) > 1:
                journey["turnaround"] = {
                    "status": "ambiguous", "risk": None, "confirmed_vehicle": False,
                    "reason": "Mehrere mögliche Vorleistungen; keine eindeutige Zugwende.",
                }
            else:
                journey["turnaround"] = {
                    "status": "unknown", "risk": None, "confirmed_vehicle": False,
                    "reason": "Keine automatisch belegbare Zugwende in aktuellen Stationstafeln.",
                }

    async def _detect_route_turnarounds(
        self, now: datetime, lines: tuple[str, ...],
        departure_boards: dict[str, list[dict]] | None = None,
        arrival_boards: dict[str, list[dict]] | None = None,
    ) -> dict:
        """Observe arrival and departure boards at both endpoints, both ways."""
        boards = []
        for station, station_id, other in (
            (self.settings["origin"], self.settings["origin_id"], self.settings["destination"]),
            (self.settings["destination"], self.settings["destination_id"], self.settings["origin"]),
        ):
            endpoint = {
                "station": station, "other": other,
                "arrivals": (
                    arrival_boards.get(station_id)
                    if arrival_boards is not None else None
                ),
                "departures": (
                    departure_boards.get(station_id)
                    if departure_boards is not None else None
                ),
            }
            if arrival_boards is None:
                try:
                    # The API caches the same station board for arrivals/departures.
                    endpoint["arrivals"] = await self.api.dbf_board(station_id, mode="arr")
                except BahnApiError:
                    pass
            if endpoint["departures"] is None and departure_boards is None:
                try:
                    endpoint["departures"] = await self.api.dbf_board(station_id, mode="dep")
                except BahnApiError:
                    pass
            boards.append(endpoint)
        return detect_turnarounds(
            now=now, endpoints=boards, lines=lines,
            min_turn_minutes=self.settings.get("min_turn_minutes", 8),
            max_turn_minutes=self.settings.get("max_turn_minutes", 60),
        )

    async def _with_turnarounds(
        self, route: dict, now: datetime, lines: tuple[str, ...],
        departure_boards: dict[str, list[dict]] | None = None,
        arrival_boards: dict[str, list[dict]] | None = None,
    ) -> dict:
        route["turnaround"] = await self._detect_route_turnarounds(
            now, lines, departure_boards, arrival_boards,
        )
        return await self._with_alerts(route, now)

    async def _route_health(self, target: datetime | None, now: datetime) -> dict:
        """Monitor configured stations and line filters in both directions.

        Queries are independent of the user's departure time, even if their
        journey is tomorrow. IRIS only reports a short station-board window;
        earlier observations are retained throughout the same day.
        """
        target_for_summary = target or now
        lines = tuple(
            item.strip() for item in str(self.settings.get("line") or "").split(",")
            if item.strip()
        )
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Never use yesterday's trains in today's corridor assessment.
        self._route_observations = {
            key: row for key, row in self._route_observations.items()
            if midnight.isoformat() <= row.get("scheduled_departure", "") < now.isoformat()
        }
        # The history flag only controls retaining past observations.
        # Live boards, turnarounds and connection alerts always run.
        history_enabled = self.settings.get("history_enabled", True)

        errors: list[str] = []
        successful: list[str] = []
        self._source_debug["station_boards"] = {}
        self._source_debug["last_collected_at"] = now.isoformat()
        self._source_debug["route"] = {
            "origin": self.settings["origin"],
            "destination": self.settings["destination"],
            "line_filter": list(lines),
        }
        boards_by_station: dict[str, list[dict]] = {}
        board_counts: dict[str, dict] = {}
        board_incidents: list[dict] = []
        upcoming: list[dict] = []
        departures_all: list[dict] = []
        arrivals_all: list[dict] = []
        arrivals_by_station: dict[str, list[dict]] = {}
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
                self._source_debug["station_boards"][f"{direction}_dep"] = {
                    "station": origin, "status": "error", "error": str(exc),
                    "direction": direction, "mode": "dep",
                }
                continue
            self._source_debug["station_boards"][f"{direction}_dep"] = board_probe(
                board, now=now, station=origin, other=destination,
                direction=direction, mode="dep", lines=lines,
            )
            successful.append(direction)
            boards_by_station[station_id] = board
            matching_departures = collect_departures(
                board, now=now, direction=direction,
                origin=origin, destination=destination, lines=lines,
            )
            departures_all.extend(matching_departures)
            board_incidents.extend(extract_board_alerts(
                board, direction=direction, origin=origin,
                destination=destination, lines=lines,
            ))
            board_counts[direction] = {
                "station": origin, "total_entries": len(board),
                "matched_in_current_window": len(matching_departures),
            }
            if history_enabled:
                for row in collect_previous(
                    board, direction=direction, origin=origin,
                    destination=destination, planned=target_for_summary, now=now,
                    lines=lines,
                ):
                    key = observation_key(row)
                    previous = self._route_observations.get(key)
                    if previous is None or row["observed_at"] >= previous.get("observed_at", ""):
                        self._route_observations[key] = row
                        observations_changed = True
            current = collect_upcoming(
                board, direction=direction, origin=origin,
                destination=destination, now=now, lines=lines,
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

        for direction, station_id, origin, destination in (
            ("same", self.settings["destination_id"],
             self.settings["origin"], self.settings["destination"]),
            ("reverse", self.settings["origin_id"],
             self.settings["destination"], self.settings["origin"]),
        ):
            try:
                board = await self.api.dbf_board(station_id, mode="arr")
            except BahnApiError as exc:
                self._source_debug["station_boards"][f"{direction}_arr"] = {
                    "station": destination, "status": "error", "error": str(exc),
                    "direction": direction, "mode": "arr",
                }
                continue
            self._source_debug["station_boards"][f"{direction}_arr"] = board_probe(
                board, now=now, station=destination, other=origin,
                direction=direction, mode="arr", lines=lines,
            )
            arrivals_by_station[station_id] = board
            arrivals_all.extend(collect_arrivals(
                board, now=now, origin=origin, destination=destination,
                direction=direction, lines=lines,
            ))

        data = summarise(
            list(self._route_observations.values()), target=target_for_summary,
            now=now, limit=3, upcoming=upcoming,
        )
        data["timetable"] = summarise_departures(
            departures_all, now=now, sources_ok=successful,
            arrivals=arrivals_all,
        )
        data["board_incidents"] = board_incidents[:20]
        data["board_counts"] = board_counts
        data["corridor"] = find_partial_bus_sections(
            now=now,
            endpoints=[
                {
                    "station": self.settings["origin"],
                    "departures": boards_by_station.get(self.settings["origin_id"]),
                    "arrivals": arrivals_by_station.get(self.settings["origin_id"]),
                },
                {
                    "station": self.settings["destination"],
                    "departures": boards_by_station.get(self.settings["destination_id"]),
                    "arrivals": arrivals_by_station.get(self.settings["destination_id"]),
                },
            ],
            lines=lines,
        )
        self._source_debug["corridor"] = data["corridor"]
        data["arrivals"] = data["timetable"]["arrivals"]
        data["departures"] = data["timetable"]["departures"]
        data["departure_count"] = data["timetable"]["departure_count"]
        own_route = (
            f"{self.settings['origin']} → {self.settings['destination']}"
        )
        other_route = (
            f"{self.settings['destination']} → {self.settings['origin']}"
        )
        data["same_direction"]["label"] = own_route
        data["reverse_direction"]["label"] = other_route
        data["direction_labels"] = {
            "same": own_route,
            "reverse": other_route,
        }
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
        return await self._with_turnarounds(
            data, now, lines, boards_by_station, arrivals_by_station,
        )

    def _turnaround_choice(self) -> str:
        """Apply the GUI choice; keep old entries without an option compatible."""
        if "turnaround_at" in self.settings:
            value = self.settings["turnaround_at"]
            return value if value in ("origin", "destination", "off", "auto") else "off"
        if not self.settings.get("turnaround", True):
            return "off"
        # No location-specific defaults: evaluate terminal turns at both ends.
        return "auto"

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
        if choice == "auto":
            # Route-wide detection runs on the same refresh, independent of
            # the scheduled train. It will attach the matching evidence later.
            return {
                **info, "status": "not_checked", "risk": None,
                "reason": "Automatische Streckenprüfung läuft",
            }
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
        inbound = candidates[0]
        result = evaluate_turnaround(
            inbound, departure, int(self.settings["min_turn_minutes"]),
        )
        from_station = self.settings["destination"]
        to_station = self.settings["origin"]
        relation = {"status": "not_checked", "confirmed_vehicle": False}
        arrival_planned = parse_time(inbound.get("plannedWhen"))
        if arrival_planned is not None:
            try:
                opposite_board = await self.api.dbf_board(
                    self.settings["destination_id"], mode="dep",
                )
            except BahnApiError as exc:
                relation = {
                    "status": "source_unavailable",
                    "reason": str(exc),
                    "confirmed_vehicle": False,
                }
            else:
                inbound_delay = inbound.get("delay")
                relation = match_inbound_origin(
                    opposite_board, line=train_line, destination=to_station,
                    arrival=arrival_planned,
                    arrival_delay_minutes=(
                        round(inbound_delay / 60)
                        if isinstance(inbound_delay, (int, float))
                        and not isinstance(inbound_delay, bool)
                        else None
                    ),
                )
        # The inbound arrival and its plausible departure are from different
        # station announcements. Do not mistake that for a vehicle identifier.
        return {
            **info, **result, "candidate_count": 1,
            "observed_incoming_train": inbound.get("observed_train"),
            "incoming_origin_station": (
                from_station if relation["status"] == "plausible" else None
            ),
            "incoming_destination_station": to_station,
            "incoming_departure_planned": relation.get("departure_planned"),
            "incoming_departure_predicted": relation.get("departure_predicted"),
            "incoming_departure_delay_minutes": relation.get(
                "departure_delay_minutes",
            ),
            "incoming_departure_delay_reasons": relation.get(
                "delay_reasons", [],
            ),
            "incoming_arrival_delay_reasons": inbound.get(
                "delay_reasons", [],
            ),
            "incoming_scheduled_travel_minutes": relation.get(
                "scheduled_travel_minutes",
            ),
            "incoming_departure_match": relation["status"],
            "incoming_departure_candidate_count": relation.get("count", 0),
            "incoming_route": (
                f"{from_station} → {to_station}"
                if relation["status"] == "plausible" else None
            ),
            "outgoing_route": f"{to_station} → {from_station}",
            "outgoing_station": to_station,
            "outgoing_destination": from_station,
            "outgoing_planned": departure.isoformat(),
            "confirmed_vehicle": False,
            "evidence": (
                "two_station_timetable_correlated_vehicle_unconfirmed"
                if relation["status"] == "plausible"
                else "same_line_arrival_candidate_vehicle_unconfirmed"
            ),
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

