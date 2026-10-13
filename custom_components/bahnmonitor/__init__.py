"""Set up Bahnmonitor with a shared API cache and persisted daily history."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import BahnApi
from .const import DOMAIN
from .coordinator import BahnCoordinator
from .gtfs import GtfsSchedule
from .route_health import observation_key

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor"]
STORAGE_VERSION = 1
LOCAL_TZ = ZoneInfo("Europe/Berlin")


async def async_setup_entry(hass, entry):
    """Create one configurable train and restore today's corridor observations."""
    settings = {**entry.data, **entry.options}
    # All entries share the per-station IRIS request cache / rate limiter.
    data = hass.data.setdefault(DOMAIN, {})
    if "api" not in data:
        data["api"] = BahnApi(async_get_clientsession(hass))
    if "gtfs" not in data:
        data["gtfs"] = GtfsSchedule(hass, async_get_clientsession(hass))
    coordinator = BahnCoordinator(hass, data["api"], settings, entry.entry_id)
    coordinator.gtfs = data["gtfs"]

    store = Store(hass, STORAGE_VERSION, f"bahnmonitor_route_history_{entry.entry_id}")
    coordinator._history_store = store
    try:
        stored = await store.async_load()
    except Exception as exc:
        _LOGGER.warning("Bahnmonitor: Konnte Streckenhistorie nicht laden: %s", exc)
        stored = None

    if isinstance(stored, dict):
        for item in (stored.get("observations") or []):
            if not isinstance(item, dict):
                continue
            try:
                coordinator._route_observations[observation_key(item)] = item
            except (KeyError, TypeError, ValueError):
                continue

    # Register the entities immediately. The first refresh must NOT wait for
    # a 12 MB GTFS download or a timed-out external 7-day API. It returns an
    # explicit "Wird geladen" snapshot; the real refresh runs after setup.
    coordinator._startup_lightweight = True
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    finally:
        coordinator._startup_lightweight = False

    # ConfigEntry-managed task does not block Home Assistant startup and is
    # cancelled automatically on unload. Concurrent entries share the GTFS
    # download lock, so there is only one feed request.
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        name=f"Bahnmonitor {entry.title} Fahrplandaten laden",
        eager_start=False,
    )

    data.setdefault("entries", {})[entry.entry_id] = coordinator
    if hasattr(hass, "services") and not hass.services.has_service(
        DOMAIN, "debug_verbindung"
    ):
        async def async_debug_verbindung(call):
            """Manually inspect non-RE-clock SEV connections via HA Actions."""
            entries = hass.data.get(DOMAIN, {}).get("entries", {})
            entry_id = str(call.data.get("entry_id") or "").strip()
            if not entry_id and len(entries) == 1:
                entry_id = next(iter(entries))
            if entry_id not in entries:
                _LOGGER.warning(
                    "Bahnmonitor Debug: Bei mehreren Einträgen bitte entry_id angeben."
                )
                return
            value = str(call.data.get("time") or "").strip()
            try:
                hh, mm = map(int, value.split(":"))
                if len(value) != 5 or not 0 <= hh < 24 or not 0 <= mm < 60:
                    raise ValueError("Zeitformat HH:MM erwartet")
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Bahnmonitor Debug: 'time' muss im Format HH:MM sein."
                )
                return
            now = datetime.now(LOCAL_TZ)
            requested = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if requested < now:
                requested += timedelta(days=1)
            await entries[entry_id].async_debug_connection_at(requested)

        hass.services.async_register(DOMAIN, "debug_verbindung", async_debug_verbindung)
    return True


async def async_unload_entry(hass, entry):
    """Persist accumulated observations before the integration is reloaded."""
    coordinator = entry.runtime_data
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    store = getattr(coordinator, "_history_store", None)
    if store is not None:
        try:
            await store.async_save({
                "observations": list(coordinator._route_observations.values()),
            })
        except Exception as exc:
            _LOGGER.warning("Bahnmonitor: Konnte Streckenhistorie nicht speichern: %s", exc)
    if result:
        container = hass.data.get(DOMAIN, {})
        entries = container.get("entries", {})
        entries.pop(entry.entry_id, None)
        if not entries and hasattr(hass, "services") and hass.services.has_service(
            DOMAIN, "debug_verbindung"
        ):
            hass.services.async_remove(DOMAIN, "debug_verbindung")
    return result
