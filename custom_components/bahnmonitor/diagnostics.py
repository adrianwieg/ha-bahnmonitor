"""Home Assistant diagnostic export for Bahnmonitor.

Contains only the configured train route and provider diagnostic counters.
It never includes authentication secrets, cookies, or raw HTTP payloads.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return a support bundle from Settings -> Devices & services."""
    coord = entry.runtime_data
    data = coord.data or {}
    return {
        "version": "0.3.5",
        "entry_title": entry.title,
        "configured_route": {
            "origin": coord.settings.get("origin"),
            "destination": coord.settings.get("destination"),
            "line": coord.settings.get("line"),
            "departure_time": coord.settings.get("departure_time"),
            "weekdays": coord.settings.get("weekdays"),
            "turnaround_at": coord.settings.get("turnaround_at", "legacy"),
            "effective_turnaround_at": coord._turnaround_choice(),
            "min_turn_minutes": coord.settings.get("min_turn_minutes"),
            "max_turn_minutes": coord.settings.get("max_turn_minutes"),
        },
        "checked_at": data.get("checked_at"),
        "provider_status": data.get("provider_status"),
        "provider_error": data.get("provider_error"),
        "retry_at": data.get("retry_at"),
        "diagnostics": data.get("diagnostics", {}),
        "journeys": data.get("journeys", []),
        "route_health": data.get("route_health", {}),
    }
