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
    assert first["route_health"]["status_code"] == "scheduled_monitoring"
    assert first["route_health"]["status"] == "Startet um 08:00"
    assert first["route_health"]["monitoring_starts_at"] == "2026-10-08T08:00:00+02:00"
    assert first["diagnostics"]["realtime_dbf"]["status"] == "skipped"
    assert first["diagnostics"]["realtime_dbf"]["reason"] == "outside_realtime_window"
    assert first["diagnostics"]["future_timetable"]["status"] == "backoff"
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


def test_iris_data_remains_fresh_during_v6_outage(monkeypatch):
    code, api_error = load_coordinator(monkeypatch)

    class MixedProvider:
        def __init__(self):
            self.dbf_calls = 0
            self.v6_calls = 0

        async def dbf_board(self, station, mode="dep"):
            self.dbf_calls += 1
            return [{
                "train": "RE 16243",
                "scheduledDeparture": "07:30",
                "delayDeparture": 6,
                "destination": "Göttingen",
            }]

        async def departures(self, station, start, duration):
            self.v6_calls += 1
            raise api_error("HTTP 503 Service Unavailable")

    api = MixedProvider()
    settings = {
        "departure_time": "07:30",
        "weekdays": "0,1,2,3,4,5,6",
        "window": 15,
        "origin_id": "8010203",
        "destination_id": "8000128",
        "origin": "Leinefelde",
        "destination": "Göttingen",
        "line": "RE 1",
        "turnaround": False,
    }
    coordinator = code.BahnCoordinator(None, api, settings, "mixed-entry")
    result = asyncio.run(coordinator._async_update_data())
    assert result["provider_status"] == "partial"
    assert result["provider_error"] and "503" in result["provider_error"]
    # The coordinator now also samples both route directions. This mock
    # has no shared API cache; the production BahnApi deduplicates calls.
    assert api.dbf_calls == 3
    assert api.v6_calls == 1, "Avoid retrying a failing backend for every future day"
    assert result["journeys"][0]["status"] == "delayed"
    assert result["journeys"][0]["source"] == "DBF/IRIS-TTS"
    assert result["diagnostics"]["realtime_dbf"]["status"] == "matched"
    assert result["diagnostics"]["realtime_dbf"]["returned_count"] == 1
    assert result["diagnostics"]["future_timetable"]["status"] == "backoff"
    assert result["journeys"][0]["stale"] is False
    assert result["journeys"][0]["line_match"] == "time_destination_unconfirmed"
    assert all(item["status"] == "unknown" for item in result["journeys"][1:])



def test_1609_realtime_starts_at_1209_not_error(monkeypatch):
    """Reproduce Home Assistant diagnostics for a 16:09 outbound at 07:52."""
    code, api_error = load_coordinator(monkeypatch)
    original_now = sys.modules["homeassistant.util.dt"].now
    sys.modules["homeassistant.util.dt"].now = lambda: datetime(
        2026, 10, 8, 7, 52, tzinfo=ZoneInfo("Europe/Berlin")
    )
    try:
        api = Provider(api_error)
        settings = {
            "departure_time": "16:09",
            "weekdays": "1,2,3",
            "window": 20,
            "origin_id": "8000128",
            "destination_id": "8010203",
            "origin": "Göttingen",
            "destination": "Leinefelde",
            "line": "RE 1",
            "turnaround": True,
        }
        coordinator = code.BahnCoordinator(None, api, settings, "1609")
        data = asyncio.run(coordinator._async_update_data())
        assert api.calls == 1
        assert data["provider_status"] == "unavailable"
        assert data["journeys"][0]["scheduled_departure"] == "2026-10-08T16:09:00+02:00"
        assert data["route_health"]["status"] == "Startet um 12:09"
        assert data["route_health"]["monitoring_starts_at"] == "2026-10-08T12:09:00+02:00"
        assert data["route_health"]["source_status"] == "not_started"
        assert data["route_health"]["sample_count"] == 0
    finally:
        sys.modules["homeassistant.util.dt"].now = original_now
