"""Train status and provider health sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([
        BahnJourneySensor(coordinator, entry),
        BahnProviderSensor(coordinator, entry),
    ])


class BahnEntity(CoordinatorEntity, SensorEntity):
    """Common device metadata for the configured monitored train."""

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

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        if data.get("provider_status") == "unavailable":
            return "Datenquelle nicht erreichbar"
        upcoming = data.get("journeys") or []
        if not upcoming:
            return "Keine geplante Fahrt"
        return upcoming[0].get("status", "unknown")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
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


class BahnProviderSensor(BahnEntity):
    _attr_icon = "mdi:cloud-check-outline"
    _attr_name = "Fahrplandienst"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_provider_status"

    @property
    def native_value(self):
        status = (self.coordinator.data or {}).get("provider_status", "not_checked")
        # "unavailable" is a reserved HA entity state and appears as
        # "Nicht verfügbar" instead of a useful provider health indication.
        return {
            "unavailable": "Gestört",
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
