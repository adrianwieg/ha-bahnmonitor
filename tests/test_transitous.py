"""MOTIS Transitous regression tests, entirely offline with synthetic responses."""
import asyncio
from datetime import datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
PKG = "bahnmonitor_transitous_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault(PKG, pkg)


def load(name):
    spec = spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    mod = module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


load("const")
provider = load("transitous")
NOW = datetime(2026, 10, 13, 20, 24, tzinfo=ZoneInfo("Europe/Berlin"))


def bus_leg(*, start="2026-10-13T21:25:00+02:00",
            end="2026-10-13T22:30:00+02:00",
            name="RE1", mode="BUS", alerts=None):
    return {
        "mode": mode,
        "routeShortName": name,
        "displayName": name,
        "from": {"name": "Leinefelde Bahnhof"},
        "to": {"name": "Göttingen Bahnhof/ZOB"},
        "scheduledStartTime": start,
        "scheduledEndTime": end,
        "startTime": start,
        "endTime": end,
        "realTime": False,
        "scheduled": True,
        "alerts": alerts or [],
    }


def walking():
    return {"mode": "WALK", "duration": 120}


def itineraries(*legs):
    return {"itineraries": [{
        "legs": list(legs),
        "transfers": 0,
        "startTime": legs[0].get("startTime") if legs else NOW.isoformat(),
        "endTime": legs[-1].get("endTime") if legs else NOW.isoformat(),
    }]}


def test_geocode_selects_named_station_and_not_nearby_suburb():
    choices = [
        {"type": "STOP", "id": "wrong", "name": "Leinefelde Ost",
         "lat": 51.40, "lon": 10.3},
        {"type": "STOP", "id": "correct", "name": "Leinefelde",
         "lat": 51.39, "lon": 10.33},
        {"type": "ADDRESS", "id": "addr", "name": "Leinefelde",
         "lat": 51.39, "lon": 10.33},
    ]
    assert provider.select_stop(choices, "Leinefelde")["id"] == "correct"
    assert provider.select_stop(choices, "Nordhausen") is None


def test_geocode_accepts_named_station_complex_and_rejects_ambiguity():
    choices = [{"type": "STOP", "id": "bus", "name": "Göttingen, Bahnhof/ZOB",
                "lat": 51.536, "lon": 9.929}]
    assert provider.select_stop(choices, "Göttingen")["id"] == "bus"
    assert provider.select_stop(choices + [{
        "type": "STOP", "id": "bus2", "name": "Göttingen Bahnhof",
        "lat": 51.536, "lon": 9.928,
    }], "Göttingen") is None


def test_geocode_accepts_municipal_station_label_from_db_navigator():
    matches = [{
        "type": "STOP", "id": "ld-bus",
        "name": "Leinefelde Bahnhof, Leinefelde-Worbis",
        "lat": 51.388, "lon": 10.33,
    }]
    assert provider.select_stop(matches, "Leinefelde")["id"] == "ld-bus"


def test_motis_direct_bus_re1_2125_with_walk_access():
    result = provider.normalize_motis(
        itineraries(walking(), bus_leg(), walking()),
        direction="same", now=NOW,
    )
    assert result["route_count"] == 1
    assert result["routes"][0]["direct_bus"] is True
    assert len(result["direct_buses"]) == 1
    bus = result["direct_buses"][0]
    assert bus["name"] == "Bus RE1"
    assert bus["scheduled_departure"] == "2026-10-13T21:25:00+02:00"
    assert bus["scheduled_arrival"] == "2026-10-13T22:30:00+02:00"
    assert bus["explicit_sev"] is False
    assert bus["source"] == "Transitous (MOTIS)"
    assert bus["real_time"] is False


def test_next_morning_bus_not_coupled_to_regular_re_departure():
    dawn = bus_leg(start="2026-10-14T04:57:00+02:00",
                   end="2026-10-14T06:02:00+02:00")
    result = provider.normalize_motis(
        itineraries(dawn), direction="same", now=NOW,
    )
    assert len(result["direct_buses"]) == 1
    assert "04:57" in result["direct_buses"][0]["scheduled_departure"]


def test_two_transit_legs_are_a_transfer_not_a_direct_bus():
    rail = bus_leg(start="2026-10-13T22:35:00+02:00",
                   end="2026-10-13T23:10:00+02:00",
                   name="RB83", mode="REGIONAL_FAST")
    result = provider.normalize_motis(
        itineraries(bus_leg(), rail), direction="same", now=NOW,
    )
    assert result["direct_buses"] == []
    assert result["route_count"] == 1
    assert result["routes"][0]["has_bus"] is True
    assert result["routes"][0]["direct_bus"] is False


def test_sev_only_when_explicitly_reported_and_alert_is_not_ignored():
    leg = bus_leg(alerts=[{
        "headerText": "Schienenersatzverkehr", "descriptionText": "Wegen Bauarbeiten"
    }])
    result = provider.normalize_motis(itineraries(leg), direction="same", now=NOW)
    assert result["direct_buses"][0]["explicit_sev"] is True
    assert "Schienenersatzverkehr" in result["direct_buses"][0]["remarks"][0]


def test_no_itineraries_is_unknown_not_an_all_clear():
    result = provider.normalize_motis({"itineraries": []}, direction="same", now=NOW)
    assert result["route_count"] == 0
    assert result["direct_buses"] == []
    with pytest.raises(provider.TransitousError):
        provider.normalize_motis({}, direction="same", now=NOW)


def test_malformed_times_are_skipped_conservatively():
    trip = bus_leg(start="not a time")
    result = provider.normalize_motis(itineraries(trip), direction="same", now=NOW)
    assert result["direct_buses"] == []


def test_user_agent_carries_project_contact_and_attribution():
    assert "Bahnmonitor/" in provider.USER_AGENT
    assert "github.com/adrianwieg/ha-bahnmonitor" in provider.USER_AGENT
    assert provider.ATTRIBUTION_URL == "https://transitous.org/sources/"
    assert provider.MIN_REFRESH >= timedelta(hours=2)


def test_motis_query_correct_version_and_station_complex_radius():
    class FakeSession:
        def __init__(self):
            self.calls = []
        def get(self, url, *, params, headers):
            self.calls.append((url, params, headers))
            class Response:
                status = 200
                class Body:
                    async def read(self, limit):
                        return b'{"itineraries": []}'
                content = Body()
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
            return Response()

    session = FakeSession()
    client = provider.TransitousClient(session)
    a = {"id": "rail-a", "name": "Leinefelde", "lat": 51.39, "lon": 10.33}
    b = {"id": "rail-b", "name": "Göttingen", "lat": 51.53, "lon": 9.93}
    result = asyncio.run(client.plan(a, b, NOW, direction="same"))
    assert result["routes"] == []
    url, params, headers = session.calls[0]
    assert url == "https://api.transitous.org/api/v6/plan"
    assert params["radius"] == 350
    assert params["time"] == NOW.isoformat()
    assert params["maxTransfers"] == 1
    assert headers["User-Agent"] == provider.USER_AGENT
