"""Readable Bahnmonitor and corridor-condition sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .presentation import format_journey, hhmm as result_time


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([
        BahnJourneySensor(coordinator, entry),
        BahnRouteHealthSensor(coordinator, entry),
        BahnProviderSensor(coordinator, entry),
    ])


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
        start = route_health.get("monitoring_starts_at")
        if (
            trip
            and trip.get("status") in ("unknown", "not_found")
            and not trip.get("source")
            and route_health.get("source_status") == "not_started"
            and start
        ):
            # Configured departure is not a verified DB timetable.
            start_hhmm = result_time(start)
            result["display_status"] = f"Echtzeit ab {start_hhmm}"
            result["display_note"] = (
                f"Zugbeobachtung startet um {start_hhmm} Uhr. "
                "Die bisherige Abfahrtszeit stammt aus deiner Konfiguration "
                "und ist nicht durch die Datenquelle bestätigt."
            )
            result["display_summary"] = (
                f"{self.coordinator.settings['line']} · "
                f"{self.coordinator.settings['origin']} → "
                f"{self.coordinator.settings['destination']} · "
                f"{result['display_planned']} Uhr konfiguriert · "
                f"Echtzeitprüfung ab {start_hhmm} Uhr"
            )
        elif trip and trip.get("status") in ("unknown", "not_found"):
            result["display_note"] = (
                "Fahrplan- oder Echtzeitdaten fehlen. "
                "Die angezeigte Abfahrtszeit ist nur konfiguriert."
            )
        else:
            result["display_note"] = (
                "Aktuelle Angabe stammt vom Fahrplandienst."
                if trip and trip.get("source") and not trip.get("stale")
                else "Daten derzeit nicht verifiziert."
            )
        result["configured_departure_time"] = self.coordinator.settings.get("departure_time")
        result["realtime_starts_at"] = start
        result["timetable_confirmed"] = bool(trip and trip.get("source"))
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
