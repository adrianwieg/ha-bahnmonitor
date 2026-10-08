"""Cancellation and indicative turnaround risk."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        BahnBinarySensor(entry.runtime_data, entry, "cancelled", "Zugausfall", "mdi:train-off"),
        BahnBinarySensor(entry.runtime_data, entry, "turnaround_risk", "Mögliche Folgeverspätung", "mdi:clock-alert-outline"),
    ])


class BahnBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, kind, name, icon):
        super().__init__(coordinator)
        self.kind = kind
        self._attr_unique_id = f"{entry.entry_id}_{kind}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = {"identifiers": {("bahnmonitor", entry.entry_id)}, "name": entry.title, "manufacturer": "Bahnmonitor"}

    @property
    def available(self):
        # Unknown upstream status must never raise a false all-clear or warning.
        data = self.coordinator.data or {}
        journeys = data.get("journeys") or []
        if not (super().available and journeys):
            return False
        trip = journeys[0]
        # A GTFS scheduled train is NOT a live cancellation confirmation.
        if trip.get("stale") or trip.get("source") not in (
            "DBF/IRIS-TTS", "db.transport.rest",
        ):
            return False
        if self.kind == "cancelled":
            # An inferred route-only match cannot confirm that *this* RE
            # service is cancelled.
            return (
                trip.get("status") not in ("unknown", "not_found")
                and trip.get("line_match", "exact") == "exact"
            )
        risk = trip.get("turnaround") or {}
        return risk.get("status") in ("possible", "no_indication")

    @property
    def is_on(self):
        journeys = (self.coordinator.data or {}).get("journeys") or []
        if not journeys:
            return None
        next_trip = journeys[0]
        if self.kind == "cancelled":
            return next_trip.get("status") == "cancelled" if next_trip.get("status") not in ("unknown", "not_found") else None
        risk = next_trip.get("turnaround") or {}
        return risk.get("risk") if risk.get("status") not in ("unknown", "not_checked") else None

    @property
    def extra_state_attributes(self):
        journeys = (self.coordinator.data or {}).get("journeys") or []
        return {"turnaround": journeys[0].get("turnaround") if journeys else None}
