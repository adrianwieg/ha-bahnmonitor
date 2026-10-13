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

    async def dbf_board(self, station, mode="dep"):
        raise self.error("DBF temporarily unreachable")


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
    assert first["route_health"]["status_code"] == "source_unavailable"
    assert first["route_health"]["source_status"] == "unavailable"
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
    assert api.dbf_calls == 5  # two arrival-board checks for automatic terminal detection
    assert api.v6_calls == 1, "Avoid retrying a failing backend for every future day"
    assert result["journeys"][0]["status"] == "delayed"
    assert result["journeys"][0]["source"] == "DBF/IRIS-TTS"
    assert result["diagnostics"]["realtime_dbf"]["status"] == "matched"
    assert result["diagnostics"]["realtime_dbf"]["returned_count"] == 1
    assert result["diagnostics"]["future_timetable"]["status"] == "backoff"
    assert result["journeys"][0]["stale"] is False
    assert result["journeys"][0]["line_match"] == "time_destination_unconfirmed"
    assert all(item["status"] == "unknown" for item in result["journeys"][1:])



def test_1609_route_watched_already_at_0752(monkeypatch):
    """A 16:09 configured journey does not block early-day corridor checks."""
    code, api_error = load_coordinator(monkeypatch)
    clock = sys.modules["homeassistant.util.dt"]
    original_now = clock.now
    clock.now = lambda: datetime(
        2026, 10, 8, 7, 52, tzinfo=ZoneInfo("Europe/Berlin")
    )

    class BoardProvider(Provider):
        def __init__(self, error):
            super().__init__(error)
            self.stations = []

        async def dbf_board(self, station, mode="dep"):
            self.stations.append(station)
            if station == "8000128":
                return [
                    {"train": "RE RE1", "destination": "Leinefelde",
                     "scheduledDeparture": "07:20", "delayDeparture": 9},
                    {"train": "RE RE11", "destination": "Leinefelde",
                     "scheduledDeparture": "08:05", "delayDeparture": 7},
                ]
            return [
                {"train": "RE RE11", "destination": "Göttingen",
                 "scheduledDeparture": "07:10", "delayDeparture": 4},
            ]

    try:
        api = BoardProvider(api_error)
        settings = {
            "departure_time": "16:09",
            "weekdays": "1,2,3",
            "window": 20,
            "origin_id": "8000128", "destination_id": "8010203",
            "origin": "Göttingen", "destination": "Leinefelde",
            "line": "RE 1", "turnaround": True,
        }
        coordinator = code.BahnCoordinator(None, api, settings, "1609")
        data = asyncio.run(coordinator._async_update_data())
        assert api.calls == 1
        assert data["provider_status"] == "partial"
        assert data["journeys"][0]["scheduled_departure"] == "2026-10-08T16:09:00+02:00"
        assert data["diagnostics"]["realtime_dbf"]["status"] == "skipped"
        assert data["route_health"]["monitoring_active"] is True
        assert data["route_health"]["source_status"] == "online"
        assert data["route_health"]["status"] == "Auffällig"
        assert data["route_health"]["sample_count"] == 1
        assert data["route_health"]["current_count"] == 0
        assert data["route_health"]["same_direction"]["count"] == 1
        assert data["route_health"]["reverse_direction"]["count"] == 0
        assert api.stations == ["8000128", "8010203", "8010203", "8000128"]
    finally:
        clock.now = original_now


def test_corridor_observations_request_coalesced_persistence(monkeypatch):
    """New daily sightings are scheduled for storage, not written every poll."""
    code, api_error = load_coordinator(monkeypatch)
    clock = sys.modules["homeassistant.util.dt"]
    original_now = clock.now
    clock.now = lambda: datetime(
        2026, 10, 8, 7, 52, tzinfo=ZoneInfo("Europe/Berlin")
    )

    class BoardProvider(Provider):
        async def dbf_board(self, station, mode="dep"):
            if station == "8000128":
                return [{
                    "train": "RE RE1", "destination": "Leinefelde",
                    "scheduledDeparture": "07:20", "delayDeparture": 11,
                }]
            return []

    class MemoryStore:
        calls = 0
        pending = None
        delay = None

        def async_delay_save(self, producer, delay):
            self.calls += 1
            self.pending = producer
            self.delay = delay

    try:
        coordinator = code.BahnCoordinator(
            None, BoardProvider(api_error), {
                "departure_time": "16:09", "weekdays": "0,1,2,3,4",
                "window": 20, "origin_id": "8000128",
                "destination_id": "8010203", "origin": "Göttingen",
                "destination": "Leinefelde", "line": "RE 1",
                "turnaround": True,
            }, "persist-test",
        )
        store = MemoryStore()
        coordinator._history_store = store
        data = asyncio.run(coordinator._async_update_data())
        assert data["route_health"]["status"] == "Auffällig"
        assert store.calls == 1
        assert store.delay == 120
        observed = store.pending()["observations"]
        assert len(observed) == 1
        assert observed[0]["delay_minutes"] == 11
        assert observed[0]["scheduled_departure"] == "2026-10-08T07:20:00+02:00"
    finally:
        clock.now = original_now



def test_configurable_turnaround_origin_is_not_hardcoded_gottingen(monkeypatch):
    code, error = load_coordinator(monkeypatch)

    class BoardProvider:
        async def dbf_board(self, station, mode="dep"):
            if mode == "arr":
                assert station == "8010203"
                return [{
                    "train": "RE RE11",
                    "scheduledArrival": "16:00",
                    "delayArrival": 12,
                    "isCancelled": False,
                }]
            assert station == "8000128" and mode == "dep"
            return []

    settings = {
        "origin": "Leinefelde",
        "destination": "Göttingen",
        "origin_id": "8010203",
        "destination_id": "8000128",
        "line": "RE 11",
        "turnaround_at": "origin",
        "min_turn_minutes": 8,
        "max_turn_minutes": 35,
    }
    coordinator = code.BahnCoordinator(None, BoardProvider(), settings, "other-end")
    result = asyncio.run(coordinator._check_dbf_turnaround(
        datetime(2026, 10, 8, 16, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        "RE 11",
    ))
    assert result["turnaround_station"] == "Leinefelde"
    assert result["candidate_count"] == 1
    assert result["risk"] is True
    assert result["confirmed_vehicle"] is False


def test_turnaround_at_destination_does_not_invent_previous_vehicle(monkeypatch):
    code, error = load_coordinator(monkeypatch)

    class NoNetwork:
        async def dbf_board(self, *args, **kwargs):
            raise AssertionError("Destination turnaround is not a pre-departure vehicle")

    settings = {
        "origin": "Leinefelde",
        "destination": "Göttingen",
        "origin_id": "8010203",
        "destination_id": "8000128",
        "line": "RE 1",
        "turnaround_at": "destination",
    }
    coordinator = code.BahnCoordinator(None, NoNetwork(), settings, "dest-turn")
    result = asyncio.run(coordinator._check_dbf_turnaround(
        datetime(2026, 10, 8, 16, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        "RE 1",
    ))
    assert result["status"] == "not_applicable"
    assert result["turnaround_station"] == "Göttingen"
    assert result["risk"] is None


def test_ambiguous_inbound_arrivals_never_create_positive_risk(monkeypatch):
    code, error = load_coordinator(monkeypatch)

    class BoardProvider:
        async def dbf_board(self, station, mode="dep"):
            return [
                {"train": "RE RE1", "scheduledArrival": "16:00", "delayArrival": 12},
                {"train": "RE RE1", "scheduledArrival": "15:58", "delayArrival": 15},
            ]

    settings = {
        "origin": "Göttingen",
        "destination": "Leinefelde",
        "origin_id": "8000128",
        "destination_id": "8010203",
        "line": "RE 1",
        "turnaround_at": "origin",
        "min_turn_minutes": 8,
        "max_turn_minutes": 35,
    }
    coordinator = code.BahnCoordinator(None, BoardProvider(), settings, "ambiguous-turn")
    result = asyncio.run(coordinator._check_dbf_turnaround(
        datetime(2026, 10, 8, 16, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        "RE 1",
    ))
    assert result["status"] == "ambiguous"
    assert result["risk"] is None
    assert result["candidate_count"] == 2



def test_static_gtfs_plan_does_not_depend_on_failing_v6(monkeypatch):
    """A confirmed scheduled train remains visible if v6 returns HTTP 503."""
    code, api_error = load_coordinator(monkeypatch)
    api = Provider(api_error)
    calls = []

    class StaticFeed:
        @property
        def diagnostic(self):
            return {"source": "GTFS Deutschland", "feed_loaded": True}

        async def find(self, settings, when, tolerance):
            calls.append(when)
            return {
                "date": when.date().isoformat(),
                "source": "GTFS Deutschland (Sollfahrplan)",
                "status": "scheduled",
                "line": "RE 1",
                "scheduled_departure": when.isoformat(),
                "predicted_departure": None,
                "realtime_confirmed": False,
                "stale": False,
                "turnaround": {"status": "not_checked", "risk": None},
            }

    coordinator = code.BahnCoordinator(None, api, {
        "departure_time": "16:09",
        "weekdays": "0,1,2,3,4,5,6",
        "window": 20,
        "origin": "Göttingen",
        "destination": "Leinefelde",
        "origin_id": "8000128",
        "destination_id": "8010203",
        "line": "RE 1",
        "history_enabled": False,
        "turnaround_at": "origin",
    }, "gtfs-demo")
    coordinator.gtfs = StaticFeed()
    result = asyncio.run(coordinator._async_update_data())
    assert api.calls == 0
    assert len(calls) == 7
    assert len(result["journeys"]) == 7
    assert all(
        item["source"] == "GTFS Deutschland (Sollfahrplan)"
        and item["status"] == "scheduled"
        and item["realtime_confirmed"] is False
        for item in result["journeys"]
    )
    assert result["diagnostics"]["gtfs_schedule"]["feed_loaded"] is True


def test_nonblocking_first_snapshot_never_uses_network(monkeypatch):
    """HA setup should not wait for a 38-second GTFS feed download."""
    code, api_error = load_coordinator(monkeypatch)

    class NoNetwork:
        async def dbf_board(self, *args, **kwargs):
            raise AssertionError("IRIS called during initial entity registration")

        async def departures(self, *args, **kwargs):
            raise AssertionError("7-day provider called during setup")

    class NoGtfs:
        async def find(self, *args, **kwargs):
            raise AssertionError("GTFS download invoked during startup")

    coordinator = code.BahnCoordinator(None, NoNetwork(), {
        "departure_time": "16:09", "weekdays": "1,2,3",
        "origin": "Göttingen", "destination": "Leinefelde",
        "origin_id": "8000128", "destination_id": "8010203",
        "line": "RE 1", "window": 15,
    }, "fast-setup")
    coordinator.gtfs = NoGtfs()
    coordinator._startup_lightweight = True
    data = asyncio.run(coordinator._async_update_data())
    assert data["provider_status"] == "not_checked"
    assert data["route_health"]["status"] == "Wird geladen"
    assert data["diagnostics"]["setup_mode"] == "non_blocking_initial_snapshot"
    assert data["journeys"]
    assert all(item["status"] == "unknown" for item in data["journeys"])
    assert coordinator._last_full_check is None


def test_gtfs_no_match_keeps_explanation_during_503(monkeypatch):
    """Missing direct GTFS trips must be described, never asserted cancelled."""
    code, api_error = load_coordinator(monkeypatch)

    class NoGtfsMatch:
        @property
        def diagnostic(self):
            return {"feed_loaded": True, "last_error": None}

        async def find_explained(self, settings, when, tolerance):
            return None, {
                "reason": "no_departure_within_search_window",
                "nearest_planned_departures": ["2026-10-08T20:09:00+02:00"],
                "nearest_origin_departures": [
                    {"time": "2026-10-08T16:09:00+02:00",
                     "last_stop": "Heilbad Heiligenstadt"},
                ],
            }

    coordinator = code.BahnCoordinator(None, Provider(api_error), {
        "departure_time": "16:09", "weekdays": "1,2,3",
        "origin": "Göttingen", "destination": "Leinefelde",
        "origin_id": "8000128", "destination_id": "8010203",
        "line": "RE 1", "window": 15, "history_enabled": False,
    }, "missing-re")
    coordinator.gtfs = NoGtfsMatch()
    data = asyncio.run(coordinator._async_update_data())
    first = data["journeys"][0]
    assert first["status"] == "unknown"
    assert first["gtfs_match_reason"] == "no_departure_within_search_window"
    assert "20:09" in first["message"]
    assert first["gtfs_origin_services"][0]["last_stop"] == "Heilbad Heiligenstadt"
    assert first.get("status") != "cancelled"
    assert first["timetable_confirmed"] is False
    assert data["provider_error"] and "503" in data["provider_error"]


def test_1609_re1_leinefelde_1518_arrival_1551_delay_17_turnaround(monkeypatch):
    """Real diagnostic 2026-10-08: exactly one minute computed turn in Göttingen."""
    code, api_error = load_coordinator(monkeypatch)
    calls = []

    class BoardProvider:
        async def dbf_board(self, station, mode="dep"):
            calls.append((station, mode))
            if station == "8000128" and mode == "arr":
                return [{
                    "train": "RE RE1",
                    "scheduledArrival": "15:51",
                    "delayArrival": 17,
                    "isCancelled": False,
                }]
            if station == "8010203" and mode == "dep":
                return [{
                    "train": "RE RE1",
                    "destination": "Göttingen",
                    "scheduledDeparture": "15:18",
                    "delayDeparture": 18,
                }]
            raise AssertionError(f"unexpected station board {station=} {mode=}")

    settings = {
        "origin": "Göttingen", "destination": "Leinefelde",
        "origin_id": "8000128", "destination_id": "8010203",
        "line": "RE 1", "turnaround_at": "origin",
        "min_turn_minutes": 10, "max_turn_minutes": 60,
    }
    coordinator = code.BahnCoordinator(None, BoardProvider(), settings, "turn-1609")
    result = asyncio.run(coordinator._check_dbf_turnaround(
        datetime(2026, 10, 8, 16, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        "RE 1",
    ))
    assert calls == [("8000128", "arr"), ("8010203", "dep")]
    assert result["status"] == "possible"
    assert result["risk"] is True
    assert result["incoming_route"] == "Leinefelde → Göttingen"
    assert result["incoming_departure_planned"] == "2026-10-08T15:18:00+02:00"
    assert result["incoming_departure_predicted"] == "2026-10-08T15:36:00+02:00"
    assert result["incoming_departure_delay_minutes"] == 18
    assert result["incoming_planned"] == "2026-10-08T15:51:00+02:00"
    assert result["incoming_predicted"] == "2026-10-08T16:08:00+02:00"
    assert result["incoming_delay_minutes"] == 17
    assert result["turnaround_station"] == "Göttingen"
    assert result["turnaround_buffer_minutes"] == 1
    assert result["minimum_turnaround_minutes"] == 10
    assert result["estimated_minimum_followup_delay_minutes"] == 9
    assert result["earliest_plausible_outgoing"] == "2026-10-08T16:18:00+02:00"
    assert result["incoming_departure_match"] == "plausible"
    assert result["incoming_departure_candidate_count"] == 1
    assert result["outgoing_route"] == "Göttingen → Leinefelde"
    assert result["confirmed_vehicle"] is False


def test_turnaround_without_correlated_opposite_departure_still_reports_risk(monkeypatch):
    code, _ = load_coordinator(monkeypatch)

    class Provider:
        async def dbf_board(self, station, mode="dep"):
            if mode == "arr":
                return [{
                    "train": "RE RE1",
                    "scheduledArrival": "15:51",
                    "delayArrival": 17,
                }]
            return []

    coordinator = code.BahnCoordinator(None, Provider(), {
        "origin": "Göttingen", "destination": "Leinefelde",
        "origin_id": "8000128", "destination_id": "8010203",
        "line": "RE 1", "turnaround_at": "origin",
        "min_turn_minutes": 10, "max_turn_minutes": 60,
    }, "unknown-origin")
    result = asyncio.run(coordinator._check_dbf_turnaround(
        datetime(2026, 10, 8, 16, 9, tzinfo=ZoneInfo("Europe/Berlin")),
        "RE 1",
    ))
    assert result["risk"] is True
    assert result["incoming_departure_match"] == "not_found"
    assert result["incoming_departure_planned"] is None
    assert result["confirmed_vehicle"] is False


def test_general_route_monitor_reports_sev_without_scheduled_train(monkeypatch):
    """The default route mode must work without line, weekdays or a time."""
    code, _ = load_coordinator(monkeypatch)

    class LiveProvider:
        def __init__(self):
            self.journey_calls = 0
            self.board_calls = 0

        async def dbf_board(self, station, mode="dep"):
            self.board_calls += 1
            return []

        async def journeys(self, origin, destination, when):
            self.journey_calls += 1
            return {"journeys": [{
                "legs": [{
                    "line": {"name": "SEV RE 1"},
                    "remarks": [{"text": "Schienenersatzverkehr zwischen den Bahnhöfen"}],
                }],
            }]}

    api = LiveProvider()
    settings = {
        "monitor_mode": "route", "name": "Beliebige Verbindung",
        "origin_id": "8010203", "destination_id": "8000128",
        "origin": "Leinefelde", "destination": "Göttingen",
        "line": "", "history_enabled": True,
    }
    coordinator = code.BahnCoordinator(None, api, settings, "route-test")
    snapshot = coordinator._startup_snapshot(NOW)
    assert snapshot["journeys"] == []
    initial = asyncio.run(coordinator._async_update_data())
    assert initial["journeys"] == []
    assert initial["provider_status"] == "online"
    assert initial["route_health"]["status_code"] == "sev"
    assert initial["route_health"]["sev_reported"] is True
    assert initial["route_health"]["alert_journey_count"] == 2
    assert api.journey_calls == 2
    assert api.board_calls == 4  # both departure boards and both arrival boards
    second = asyncio.run(coordinator._async_update_data())
    assert second["route_health"]["status_code"] == "sev"
    assert api.journey_calls == 2, "Cache recent route alerts, avoid API flooding"


def test_route_setup_can_recover_missing_eva_ids_without_reconfiguration(monkeypatch):
    """Temporary /locations outages must not turn setup into a dead-end."""
    code, api_error = load_coordinator(monkeypatch)

    class DeferredProvider:
        failing = True
        board_calls = 0

        async def resolve_station(self, station):
            if self.failing:
                raise api_error("station search HTTP 503")
            return {
                "Erfurt Hbf": ("8010101", "Erfurt Hbf"),
                "Gotha": ("8010132", "Gotha"),
            }[station]

        async def dbf_board(self, station, mode="dep"):
            self.board_calls += 1
            return []

        async def journeys(self, origin, destination, now):
            return {"journeys": []}

    provider = DeferredProvider()
    settings = {
        "monitor_mode": "route", "origin": "Erfurt Hbf",
        "destination": "Gotha", "origin_id": "", "destination_id": "",
        "station_lookup_pending": True, "line": "",
    }
    coordinator = code.BahnCoordinator(None, provider, settings, "pending-ids")

    first = asyncio.run(coordinator._async_update_data())
    assert first["provider_status"] == "unavailable"
    assert first["route_health"]["status_code"] == "station_lookup_pending"
    assert first["diagnostics"]["station_lookup"]["status"] == "pending"
    assert first["retry_at"] is not None
    assert provider.board_calls == 0

    provider.failing = False
    coordinator._station_retry_at = NOW - timedelta(minutes=1)
    recovered = asyncio.run(coordinator._async_update_data())
    assert settings["origin_id"] == "8010101"
    assert settings["destination_id"] == "8010132"
    assert settings["station_lookup_pending"] is False
    assert recovered["route_health"]["status_code"] != "station_lookup_pending"
    assert provider.board_calls > 0


def test_manual_time_probe_does_not_infer_bus_schedule_from_train(monkeypatch):
    """Debug queries retain actual replacement bus times, independent of trains."""
    coordinator_module, error = load_coordinator(monkeypatch)

    class Provider:
        called_at = []
        async def journeys(self, start, destination, when):
            self.called_at.append(when.isoformat())
            return {"journeys": [{"legs": [{
                "line": {"name": "Bus SEV RE1", "product": "bus"},
                "origin": {"name": "Leinefelde"},
                "destination": {"name": "Eichenberg"},
                "plannedDeparture": "2026-10-08T12:35:00+02:00",
            }]}]}

    api = Provider()
    coord = coordinator_module.BahnCoordinator(None, api, {
        "name": "Alle Linien", "monitor_mode": "route",
        "origin": "Leinefelde", "destination": "Göttingen",
        "origin_id": "8010203", "destination_id": "8000128", "line": "",
    }, "test-debug")
    requested = NOW.replace(hour=12, minute=0)
    result = asyncio.run(coord.async_debug_connection_at(requested))
    assert len(api.called_at) == 2
    assert api.called_at == [requested.isoformat()] * 2
    leg = result["directions"]["same"]["journeys"][0]["legs"][0]
    assert leg["planned_departure"] == "2026-10-08T12:35:00+02:00"
    assert leg["explicit_sev"] is True
    assert leg["destination"] == "Eichenberg"
    assert coord._source_debug["manual_probes"][-1]["requested_at"] == requested.isoformat()


def test_manual_debug_provider_failure_visible_but_no_false_sev(monkeypatch):
    coordinator_module, error = load_coordinator(monkeypatch)

    class Offline:
        async def journeys(self, start, end, when):
            raise error("HTTP 503")

    coord = coordinator_module.BahnCoordinator(None, Offline(), {
        "name": "Strecke", "monitor_mode": "route",
        "origin": "A", "destination": "B",
        "origin_id": "1", "destination_id": "2", "line": "",
    }, "test-offline")
    result = asyncio.run(coord.async_debug_connection_at(NOW))
    assert result["directions"]["same"]["status"] == "error"
    assert "503" in result["directions"]["reverse"]["error"]
    assert len(coord._source_debug["manual_probes"]) == 1



def test_real_bus_stops_probe_catches_2125_and_next_day_0457(monkeypatch):
    """Replacement buses must not depend on train EVA IDs or regular RE times."""
    coord_module, api_error = load_coordinator(monkeypatch)
    t = datetime(2026, 10, 13, 19, 29, tzinfo=ZoneInfo("Europe/Berlin"))
    origin_stop = "Leinefelde Bahnhof, Leinefelde-Worbis"
    destination_stop = "Bahnhof/ZOB, Göttingen"

    class BusApi:
        lookups = []
        queries = []

        async def resolve_public_stop(self, query):
            self.lookups.append(query)
            return {
                origin_stop: ("bus-leinefelde", origin_stop),
                destination_stop: ("bus-goettingen", destination_stop),
            }[query]

        async def bus_journeys(self, first, last, when):
            self.queries.append((first, last, when.hour))
            if first != "bus-leinefelde":
                return {"journeys": []}
            day = "2026-10-13" if when.hour == 19 else "2026-10-14"
            depart = "21:25" if when.hour == 19 else "04:57"
            arrive = "22:30" if when.hour == 19 else "06:02"
            return {"journeys": [{"legs": [{
                "line": {"name": "Bus RE1", "mode": "bus", "product": "bus"},
                "origin": {"name": origin_stop},
                "destination": {"name": destination_stop},
                "plannedDeparture": f"{day}T{depart}:00+02:00",
                "plannedArrival": f"{day}T{arrive}:00+02:00",
            }]}]}

    api = BusApi()
    coord = coord_module.BahnCoordinator(None, api, {
        "origin": "Leinefelde", "destination": "Göttingen",
        "origin_id": "rail-leinefelde", "destination_id": "rail-goettingen",
        "bus_origin_stop": origin_stop, "bus_destination_stop": destination_stop,
        "monitor_mode": "route", "line": "",
    }, "bus-test")
    report = asyncio.run(coord._bus_stop_route(t))
    assert report["status"] == "online"
    assert report["service_count"] == 2
    assert [row["scheduled_departure"][11:16] for row in report["services"]] == [
        "21:25", "04:57",
    ]
    assert len(api.lookups) == 2
    assert len(api.queries) == 4
    assert api.queries[0][0] == "bus-leinefelde"
    assert api.queries[0][2] == 19
    assert api.queries[1][2] == 4
    # Cached for one hour: public provider is not flooded.
    assert asyncio.run(coord._bus_stop_route(t + timedelta(minutes=10))) is report
    assert len(api.queries) == 4


def test_bus_service_outage_does_not_generate_fake_departures(monkeypatch):
    coord_module, api_error = load_coordinator(monkeypatch)

    class Offline:
        async def resolve_public_stop(self, name):
            raise api_error("HTTP 503")

        async def bus_journeys(self, first, last, when):
            raise AssertionError("No bus route query without valid stop IDs")

    coord = coord_module.BahnCoordinator(None, Offline(), {
        "origin": "A", "destination": "B",
        "origin_id": "a", "destination_id": "b",
        "bus_origin_stop": "Bus A", "bus_destination_stop": "Bus B",
        "line": "", "monitor_mode": "route",
    }, "bus-error")
    result = asyncio.run(coord._bus_stop_route(NOW))
    assert result["status"] == "unavailable"
    assert result["services"] == []
    assert "503" in result["errors"][0]



def test_route_automatically_discovers_bus_stops_without_options(monkeypatch):
    """Leinefelde -> Göttingen is one configured corridor, rail and bus combined."""
    coord_module, api_error = load_coordinator(monkeypatch)
    t = datetime(2026, 10, 13, 19, 29, tzinfo=ZoneInfo("Europe/Berlin"))

    class StationComplexProvider:
        lookups = []
        journey_requests = []

        async def resolve_bus_stop_for_station(self, station, station_id):
            self.lookups.append((station, station_id))
            return {
                "Leinefelde": ("bus-ld", "Leinefelde Bahnhof, Leinefelde-Worbis"),
                "Göttingen": ("bus-goe", "Göttingen, Bahnhof/ZOB"),
            }[station]

        async def bus_journeys(self, first, last, when):
            self.journey_requests.append((first, last, when.hour))
            if first != "bus-ld":
                return {"journeys": []}
            return {"journeys": [{
                "legs": [{
                    "line": {"name": "Bus RE1", "product": "bus"},
                    "origin": {"name": "Leinefelde Bahnhof", "id": "bus-ld"},
                    "destination": {"name": "Bahnhof/ZOB, Göttingen", "id": "bus-goe"},
                    "plannedDeparture": "2026-10-13T21:25:00+02:00",
                    "plannedArrival": "2026-10-13T22:30:00+02:00",
                }],
            }]}

    api = StationComplexProvider()
    config = {
        "monitor_mode": "route", "origin": "Leinefelde", "destination": "Göttingen",
        "origin_id": "8010203", "destination_id": "8000128", "line": "RE 1",
    }
    coord = coord_module.BahnCoordinator(None, api, config, "automatic-complex")
    observed = asyncio.run(coord._bus_stop_route(t))
    assert observed["resolution"] == "automatic"
    assert observed["status"] == "online"
    assert observed["service_count"] == 1
    assert observed["services"][0]["name"] == "Bus RE1"
    assert observed["bus_stops"]["configured_route"] == "Leinefelde → Göttingen"
    assert observed["bus_stops"]["origin_stop_id"] == "bus-ld"
    assert observed["bus_stops"]["destination_stop_id"] == "bus-goe"
    assert api.lookups == [
        ("Leinefelde", "8010203"),
        ("Göttingen", "8000128"),
    ]
    assert asyncio.run(coord._bus_stop_route(t + timedelta(minutes=10))) is observed
    assert len(api.journey_requests) == 4
