"""Readable Bahnmonitor and corridor-condition sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .presentation import format_journey, hhmm as result_time


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [
        BahnRouteHealthSensor(coordinator, entry),
        BahnRouteDepartureSensor(coordinator, entry, "same"),
        BahnRouteDepartureSensor(coordinator, entry, "reverse"),
        BahnRouteArrivalSensor(coordinator, entry, "same"),
        BahnRouteArrivalSensor(coordinator, entry, "reverse"),
        BahnTurnaroundSensor(coordinator, entry),
        BahnRouteMessagesSensor(coordinator, entry),
        BahnProviderSensor(coordinator, entry),
    ]
    if coordinator.settings.get("monitor_mode", "train") != "route":
        entities.insert(0, BahnJourneySensor(coordinator, entry))
    async_add_entities(entities)


class BahnEntity(CoordinatorEntity, SensorEntity):
    """Stable device metadata across the configured trip's sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {("bahnmonitor", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Bahnmonitor",
        }


class BahnJourneySensor(BahnEntity):
    _attr_icon = "mdi:train"
    _attr_name = "Nächste Fahrt"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_journey"

    def _display(self):
        data = self.coordinator.data or {}
        trips = data.get("journeys") or []
        trip = trips[0] if trips else None
        result = format_journey(
            trip,
            line=self.coordinator.settings["line"],
            origin=self.coordinator.settings["origin"],
            destination=self.coordinator.settings["destination"],
        )
        route_health = data.get("route_health") or {}
        start = route_health.get("own_train_realtime_starts_at")
        if (
            trip
            and trip.get("status") in ("unknown", "not_found")
            and not trip.get("source")
            and start
            and data.get("diagnostics", {}).get("realtime_dbf", {}).get("status") == "skipped"
        ):
            # Configured departure is not a verified DB timetable.
            start_hhmm = result_time(start)
            result["display_status"] = f"Zugprognose ab {start_hhmm}"
            result["display_note"] = (
                "Die Streckenlage wird bereits ganztägig beobachtet. "
                f"Dein konkreter Zug wird ab {start_hhmm} Uhr in Echtzeit geprüft. "
                "Die angegebene Abfahrtszeit ist noch nicht durch Fahrplandaten bestätigt."
            )
            result["display_summary"] = (
                f"{self.coordinator.settings['line']} · "
                f"{self.coordinator.settings['origin']} → "
                f"{self.coordinator.settings['destination']} · "
                f"{result['display_planned']} Uhr konfiguriert · "
                f"Zugprognose ab {start_hhmm} Uhr"
            )
        elif trip and trip.get("status") in ("unknown", "not_found"):
            result["display_note"] = (
                "Fahrplan- oder Echtzeitdaten fehlen. "
                "Die angezeigte Abfahrtszeit ist nur konfiguriert."
            )
        else:
            static_plan = bool(
                trip and str(trip.get("source", "")).startswith("GTFS")
            )
            result["display_note"] = (
                "Nur Sollfahrplan aus GTFS Deutschland – keine Echtzeitbestätigung."
                if static_plan and not trip.get("stale")
                else "Aktuelle Zugmeldung des Fahrplandienstes."
                if trip and trip.get("source") and not trip.get("stale")
                else "Daten derzeit nicht verifiziert."
            )
        result["configured_departure_time"] = self.coordinator.settings.get("departure_time")
        result["realtime_starts_at"] = start
        result["timetable_confirmed"] = bool(trip and trip.get("source"))
        result["realtime_checked"] = bool(
            trip and not trip.get("stale")
            and trip.get("source") in ("DBF/IRIS-TTS", "db.transport.rest")
        )
        return result

    @property
    def native_value(self):
        trips = (self.coordinator.data or {}).get("journeys") or []
        if not trips:
            return "Keine geplante Fahrt"
        return self._display()["display_status"]

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            **self._display(),
            "journeys": data.get("journeys", []),
            "provider_status": data.get("provider_status", "not_checked"),
            "provider_error": data.get("provider_error"),
            "retry_at": data.get("retry_at"),
            "last_successful_update": data.get("last_successful_update"),
            "checked_at": data.get("checked_at"),
            "source": (data.get("journeys") or [{}])[0].get("source"),
            "origin": self.coordinator.settings["origin"],
            "destination": self.coordinator.settings["destination"],
        }


class BahnRouteHealthSensor(BahnEntity):
    _attr_name = "Streckenlage"
    _attr_icon = "mdi:train-car"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_route_health"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("route_health", {}).get(
            "status", "Keine Daten"
        )

    @property
    def extra_state_attributes(self):
        # Current realtime evidence only. Never present corridor context
        # as a vehicle circulation or guaranteed future delay forecast.
        return dict((self.coordinator.data or {}).get("route_health") or {})


class BahnProviderSensor(BahnEntity):
    _attr_icon = "mdi:cloud-check-outline"
    _attr_name = "Fahrplandienst"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_provider_status"

    @property
    def native_value(self):
        status = (self.coordinator.data or {}).get("provider_status", "not_checked")
        if status == "unavailable":
            route_health = (self.coordinator.data or {}).get("route_health") or {}
            if route_health.get("source_status") == "not_started":
                return "7-Tage-Auskunft gestört"
            return "Fahrplandaten gestört"
        return {
            "partial": "Teilweise verfügbar",
            "online": "Online",
            "not_checked": "Noch nicht geprüft",
        }.get(status, status)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "error": data.get("provider_error"),
            "retry_at": data.get("retry_at"),
            "last_successful_update": data.get("last_successful_update"),
            "checked_at": data.get("checked_at"),
            "diagnostics": data.get("diagnostics", {}),
        }


class BahnRouteDepartureSensor(BahnEntity):
    """The nearest observed departure for either direction of the route."""

    _attr_icon = "mdi:train"

    def __init__(self, coordinator, entry, direction: str):
        super().__init__(coordinator, entry)
        self.direction = direction
        self._attr_name = "Nächste Abfahrt" if direction == "same" else "Nächste Rückfahrt"
        self._attr_unique_id = f"{entry.entry_id}_route_departure_{direction}"

    @property
    def native_value(self):
        report = (self.coordinator.data or {}).get("route_health", {}).get("timetable") or {}
        key = "next_departure" if self.direction == "same" else "next_reverse_departure"
        row = report.get(key)
        if row:
            from .presentation import hhmm
            stamp = hhmm(row.get("predicted_departure") or row["planned_departure"])
            delay = row.get("delay_minutes")
            suffix = f" (+{delay} Min)" if isinstance(delay, (int, float)) and delay > 0 else ""
            return f"{row['line']} {stamp}{suffix}"
        return "Keine aktuelle Abfahrt" if report.get("source_status") == "online" else "Daten fehlen"

    @property
    def extra_state_attributes(self):
        route = (self.coordinator.data or {}).get("route_health") or {}
        report = route.get("timetable") or {}
        key = "next_departure" if self.direction == "same" else "next_reverse_departure"
        return {
            "direction": route.get("direction_labels", {}).get(self.direction),
            "next_departure": report.get(key),
            "departures": [
                row for row in report.get("departures", [])
                if row.get("direction") == self.direction
            ],
            "source_status": report.get("source_status"),
            "scope": report.get("scope"),
        }


class BahnTurnaroundSensor(BahnEntity):
    """Automatically inferred turns, not guaranteed physical vehicle matches."""

    _attr_icon = "mdi:train-variant"
    _attr_name = "Zugwenden"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_automatic_turnaround"

    @property
    def native_value(self):
        report = (self.coordinator.data or {}).get("route_health", {}).get("turnaround") or {}
        return {
            "plausible": "Mögliche Zugwende erkannt",
            "ambiguous": "Zugwende nicht eindeutig",
            "not_found": "Keine Zugwende erkannt",
            "source_unavailable": "Datenquelle gestört",
        }.get(report.get("status"), "Noch nicht geprüft")

    @property
    def extra_state_attributes(self):
        return dict((self.coordinator.data or {}).get("route_health", {}).get("turnaround") or {})


class BahnRouteMessagesSensor(BahnEntity):
    """Visible route notices including SEV and partial-source diagnostics."""

    _attr_icon = "mdi:alert-circle-outline"
    _attr_name = "Verkehrsmeldungen"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_route_messages"

    @property
    def native_value(self):
        route = (self.coordinator.data or {}).get("route_health") or {}
        if route.get("alert_source_status") not in ("online", "partial"):
            return "Datenquelle gestört"
        count = len(route.get("incidents") or [])
        return f"{count} Meldung(en)" if count else "Keine Meldungen übermittelt"

    @property
    def extra_state_attributes(self):
        route = (self.coordinator.data or {}).get("route_health") or {}
        return {
            "incidents": route.get("incidents", []),
            "sev_reported": route.get("sev_reported", False),
            "source_status": route.get("alert_source_status"),
            "last_checked": route.get("alert_checked_at"),
            "notice": route.get("alert_note"),
            "scope": route.get("alert_scope"),
        }


class BahnRouteArrivalSensor(BahnEntity):
    """Observed arrival at the other endpoint of the monitored corridor."""

    _attr_icon = "mdi:train-car"

    def __init__(self, coordinator, entry, direction: str):
        super().__init__(coordinator, entry)
        self.direction = direction
        self._attr_name = "Nächste Ankunft" if direction == "same" else "Nächste Rückankunft"
        self._attr_unique_id = f"{entry.entry_id}_route_arrival_{direction}"

    @property
    def native_value(self):
        timetable = (self.coordinator.data or {}).get("route_health", {}).get("timetable") or {}
        key = "next_arrival" if self.direction == "same" else "next_reverse_arrival"
        row = timetable.get(key)
        if row:
            stamp = result_time(row.get("predicted_arrival") or row["planned_arrival"])
            delay = row.get("delay_minutes")
            suffix = f" (+{delay} Min)" if isinstance(delay, (int, float)) and delay > 0 else ""
            return f"{row['line']} {stamp}{suffix}"
        return "Keine aktuelle Ankunft" if timetable.get("source_status") == "online" else "Daten fehlen"

    @property
    def extra_state_attributes(self):
        route = (self.coordinator.data or {}).get("route_health") or {}
        timetable = route.get("timetable") or {}
        key = "next_arrival" if self.direction == "same" else "next_reverse_arrival"
        return {
            "direction": route.get("direction_labels", {}).get(self.direction),
            "next_arrival": timetable.get(key),
            "arrivals": [
                row for row in timetable.get("arrivals", [])
                if row.get("direction") == self.direction
            ],
            "source_status": timetable.get("source_status"),
            "scope": timetable.get("scope"),
        }
