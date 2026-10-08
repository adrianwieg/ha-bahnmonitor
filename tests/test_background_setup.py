"""Home Assistant setup contract: GTFS download must not block entry setup."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"


def _module(monkeypatch, name, **values):
    mod = types.ModuleType(name)
    for key, value in values.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def test_initial_setup_registers_entities_before_slow_network(monkeypatch):
    _module(monkeypatch, "homeassistant")
    _module(monkeypatch, "homeassistant.helpers")

    class FakeStore:
        def __init__(self, *args):
            self.saved = []

        async def async_load(self):
            return None

        async def async_save(self, data):
            self.saved.append(data)

    _module(
        monkeypatch, "homeassistant.helpers.storage", Store=FakeStore,
    )
    _module(
        monkeypatch, "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: object(),
    )
    pkg = "bahnmonitor_setup_test"
    package = _module(monkeypatch, pkg)
    package.__path__ = [str(ROOT)]

    class Api:
        def __init__(self, session):
            self.session = session

    class Gtfs:
        def __init__(self, hass, session):
            self.session = session

    class Coordinator:
        def __init__(self, hass, api, settings, entry_id):
            self._startup_lightweight = False
            self._route_observations = {}
            self._history_store = None
            self.first_refreshes = 0
            self.real_refreshes = 0
            self.gtfs = None

        async def async_config_entry_first_refresh(self):
            assert self._startup_lightweight is True
            self.first_refreshes += 1

        async def async_refresh(self):
            assert self._startup_lightweight is False
            self.real_refreshes += 1

    _module(monkeypatch, f"{pkg}.api", BahnApi=Api)
    _module(monkeypatch, f"{pkg}.const", DOMAIN="bahnmonitor")
    _module(monkeypatch, f"{pkg}.coordinator", BahnCoordinator=Coordinator)
    _module(monkeypatch, f"{pkg}.gtfs", GtfsSchedule=Gtfs)
    _module(monkeypatch, f"{pkg}.route_health",
            observation_key=lambda item: item["train"])

    spec = importlib.util.spec_from_file_location(
        f"{pkg}.integration", ROOT / "__init__.py",
    )
    integration = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, integration)
    spec.loader.exec_module(integration)

    class HA:
        data = {}

        class ConfigEntries:
            async def async_forward_entry_setups(self, entry, platforms):
                assert entry.runtime_data.first_refreshes == 1
                assert entry.runtime_data.real_refreshes == 0

            async def async_unload_platforms(self, entry, platforms):
                return True

        config_entries = ConfigEntries()

    class Entry:
        data = {"name": "RE1"}
        options = {}
        entry_id = "abc"
        title = "RE1 1609"
        runtime_data = None
        background = None
        background_options = None

        def async_create_background_task(self, hass, coro, name, eager_start):
            self.background = coro
            self.background_options = (name, eager_start)

    async def scenario():
        hass, entry = HA(), Entry()
        assert await integration.async_setup_entry(hass, entry) is True
        assert entry.runtime_data._startup_lightweight is False
        assert entry.runtime_data.first_refreshes == 1
        assert entry.runtime_data.real_refreshes == 0
        assert entry.background_options[1] is False
        # HA runs this task in the background after entry setup returns.
        await entry.background
        assert entry.runtime_data.real_refreshes == 1
        assert await integration.async_unload_entry(hass, entry) is True

    asyncio.run(scenario())
