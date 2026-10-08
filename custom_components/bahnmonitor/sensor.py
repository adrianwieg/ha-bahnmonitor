"""Per-service sensor with 7-day upcoming list."""
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BahnJourneySensor(entry.runtime_data, entry)])


class BahnJourneySensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:train"
    _attr_has_entity_name = True
    _attr_name = "Nächste Fahrt"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_journey"
        self._attr_device_info = {"identifiers": {("bahnmonitor", entry.entry_id)}, "name": entry.title, "manufacturer": "Bahnmonitor"}

    @property
    def native_value(self):
        upcoming = (self.coordinator.data or {}).get("journeys", [])
        if not upcoming:
            return "Keine geplante Fahrt"
        return upcoming[0].get("status", "unknown")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {"journeys": data.get("journeys", []), "checked_at": data.get("checked_at"), "partial_errors": data.get("partial_errors", []), "origin": self.coordinator.settings["origin"], "destination": self.coordinator.settings["destination"]}
