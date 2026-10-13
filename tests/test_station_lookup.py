"""Station resolution does not depend on a single live endpoint."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
PKG = "bahnmonitor_lookup_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault(PKG, pkg)


def load(name):
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.{name}", ROOT / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("const")
load("stations")
api_module = load("api")


def resolve(fake, station):
    api = api_module.BahnApi(None)
    api._get = fake
    return asyncio.run(api.resolve_station(station))


def test_prefers_static_stations_without_hafas_locations():
    calls = []

    async def fake(path, params):
        calls.append(path)
        assert path == "/stations"
        assert params["query"] == "Erfurt Hbf"
        return {
            "8010101": {"name": "Erfurt Hbf", "type": "station", "id": "8010101"}
        }

    assert resolve(fake, "Erfurt Hbf") == ("8010101", "Erfurt Hbf")
    assert calls == ["/stations"]


def test_falls_back_to_locations_if_static_api_is_unreachable():
    calls = []

    async def fake(path, params):
        calls.append(path)
        if path == "/stations":
            raise api_module.BahnApiError("HTTP 503")
        return [
            {"name": "Bad Langensalza", "type": "stop", "id": "8010012"}
        ]

    assert resolve(fake, "Bad Langensalza") == ("8010012", "Bad Langensalza")
    assert calls == ["/stations", "/locations"]


def test_truly_unavailable_lookup_is_transient_error():
    async def fake(path, params):
        raise api_module.BahnApiError("HTTP 503")

    with pytest.raises(api_module.BahnApiError, match="nicht erreichbar"):
        resolve(fake, "Erfurt Hbf")


def test_successful_but_unmatched_station_is_not_an_outage():
    async def fake(path, params):
        return []

    with pytest.raises(api_module.StationNotFound, match="Kein eindeutig"):
        resolve(fake, "Gibt Es Nicht Hbf")


def test_numeric_eva_needs_no_provider():
    async def fake(path, params):
        raise AssertionError("numeric EVA ID should not query provider")

    assert resolve(fake, "8010101") == ("8010101", "8010101")


def test_valid_small_station_is_deferred_when_only_static_index_responds():
    async def fake(path, params):
        if path == "/stations":
            return {}
        raise api_module.BahnApiError("live locations HTTP 503")

    with pytest.raises(api_module.BahnApiError, match="teilweise/nicht"):
        resolve(fake, "Dorfstation")



def test_different_bus_platform_stop_looked_up_with_dbweb_profile():
    from_stop = "Leinefelde Bahnhof, Leinefelde-Worbis"

    async def fake(path, params):
        assert path == "/locations"
        assert params["profile"] == "dbweb"
        assert params["query"] == from_stop
        return [{"type": "stop", "id": "12345678", "name": from_stop}]

    api = api_module.BahnApi(None)
    api._get = fake
    assert asyncio.run(api.resolve_public_stop(from_stop)) == ("12345678", from_stop)


def test_public_bus_router_uses_actual_stop_ids_and_buses():
    calls = []

    async def fake(path, params):
        calls.append((path, params))
        return {"journeys": []}

    api = api_module.BahnApi(None)
    api._get = fake
    when = __import__("datetime").datetime.fromisoformat("2026-10-13T21:00:00+02:00")
    assert asyncio.run(api.bus_journeys("11111", "22222", when)) == {"journeys": []}
    path, params = calls[0]
    assert path == "/journeys"
    assert params["from"] == "11111"
    assert params["to"] == "22222"
    assert params["profile"] == "dbweb"
    assert params["bus"] == "true"
    assert params["departure"] == when.isoformat()



def test_bus_stop_city_order_aliases_but_no_rail_station_substitution():
    from_name = "Leinefelde Bahnhof, Leinefelde-Worbis"
    to_name = "Bahnhof/ZOB, Göttingen"
    resolver = sys.modules[f"{PKG}.stations"]
    origin = [
        {"type": "station", "id": "8010203", "name": "Leinefelde"},
        {"type": "stop", "id": "1234567", "name": "Leinefelde, Bahnhof",
         "products": {"bus": True}},
    ]
    dest = [
        {"type": "station", "id": "8000128", "name": "Göttingen"},
        {"type": "stop", "id": "7654321", "name": "Göttingen, Bahnhof/ZOB",
         "products": {"bus": True}},
    ]
    assert resolver.select_public_transport_stop(origin, from_name) == (
        "1234567", "Leinefelde, Bahnhof"
    )
    assert resolver.select_public_transport_stop(dest, to_name) == (
        "7654321", "Göttingen, Bahnhof/ZOB"
    )
    assert resolver.select_public_transport_stop(
        [{"type": "station", "id": "8000128", "name": "Göttingen"}], to_name,
    ) is None
