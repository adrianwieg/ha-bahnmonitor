"""Outage recovery contract, using Home Assistant stubs to exercise the real coordinator."""
import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
NOW = datetime(2026, 10, 8, 6, 50, tzinfo=ZoneInfo("Europe/Berlin"))


def _stub(monkeypatch, name, **attributes):
    result = types.ModuleType(name)
    for attr, value in attributes.items():
        setattr(result, attr, value)
    monkeypatch.setitem(sys.modules, name, result)
    return result


def load_coordinator(monkeypatch):
    class FakeDataUpdateCoordinator:
        def __init__(self, hass, logger, name, update_interval):
            self.data = None
            self.update_interval = update_interval

    _stub(monkeypatch, "homeassistant")
    _stub(monkeypatch, "homeassistant.helpers")
    _stub(
        monkeypatch, "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=FakeDataUpdateCoordinator,
    )
    _stub(monkeypatch, "homeassistant.util")
    clock = _stub(monkeypatch, "homeassistant.util.dt", now=lambda: NOW)
    sys.modules["homeassistant.util"].dt = clock

    class FakeApiError(Exception):
        pass

    pkg = _stub(monkeypatch, "bahnmonitor_test")
    pkg.__path__ = [str(ROOT)]
    _stub(
        monkeypatch, "bahnmonitor_test.api",
        BahnApi=object, BahnApiError=FakeApiError,
    )

    spec = importlib.util.spec_from_file_location(
        "bahnmonitor_test.logic", ROOT / "logic.py",
    )
    logic = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "bahnmonitor_test.logic", logic)
    spec.loader.exec_module(logic)

    spec = importlib.util.spec_from_file_location(
        "bahnmonitor_test.coordinator", ROOT / "coordinator.py",
    )
    coordinator = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "bahnmonitor_test.coordinator", coordinator)
    spec.loader.exec_module(coordinator)
    return coordinator, FakeApiError


class Provider:
    """Initially returns HTTP 503, then recovers with an empty timetable."""

    def __init__(self, error):
        self.calls = 0
        self.failing = True
        self.error = error

    async def departures(self, station, start, duration):
        self.calls += 1
        if self.failing:
            raise self.error("HTTP 503 Service Unavailable")
        return []


def test_503_keeps_entry_loaded_and_throttles_requests(monkeypatch):
    code, api_error = load_coordinator(monkeypatch)
    api = Provider(api_error)
    settings = {
        "departure_time": "12:00",
        "weekdays": "0,1,2,3,4,5,6",
        "window": 20,
        "origin_id": "8010203",
        "destination_id": "8000128",
        "origin": "Leinefelde",
        "destination": "Göttingen",
        "line": "RE 1",
        "turnaround": False,
    }
    coordinator = code.BahnCoordinator(None, api, settings, "test-entry")

    first = asyncio.run(coordinator._async_update_data())
    assert first["provider_status"] == "unavailable"
    assert first["provider_error"] and "503" in first["provider_error"]
    assert api.calls == 1
    assert first["retry_at"] is not None
    assert len(first["journeys"]) == 7
    assert all(j["status"] == "unknown" and j["stale"] for j in first["journeys"])

    second = asyncio.run(coordinator._async_update_data())
    assert second["provider_status"] == "unavailable"
    assert api.calls == 1, "Must not flood the API during backoff"

    api.failing = False
    coordinator._blocked_until = NOW - timedelta(minutes=1)
    recovered = asyncio.run(coordinator._async_update_data())
    assert recovered["provider_status"] == "online"
    assert recovered["provider_error"] is None
    assert api.calls > 1
    assert all(not journey["stale"] for journey in recovered["journeys"])
    assert all(j["status"] == "not_found" for j in recovered["journeys"])
    assert all(j["status"] != "cancelled" for j in recovered["journeys"])
