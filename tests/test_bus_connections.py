"""Actual replacement buses stop at separate stops, not railway EVA stations."""
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
PKG = "bahnmonitor_bus_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault(PKG, pkg)


def load(name):
    spec = spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("dbf")
load("disruptions")
bus = load("bus_connections")
NOW = datetime(2026, 10, 13, 19, 29, tzinfo=ZoneInfo("Europe/Berlin"))
FROM = "Leinefelde Bahnhof, Leinefelde-Worbis"
TO = "Bahnhof/ZOB, Göttingen"


def trip(departure, arrival, *, name="Bus RE1", mode="bus", origin=FROM):
    return {"legs": [{
        "line": {"name": name, "mode": mode, "product": mode},
        "origin": {"name": origin}, "destination": {"name": TO},
        "plannedDeparture": departure, "plannedArrival": arrival,
        "departure": departure, "arrival": arrival, "remarks": [],
    }]}


def collect(journeys):
    return bus.collect_bus_connections(
        {"journeys": journeys}, direction="same", origin_stop=FROM,
        destination_stop=TO, now=NOW,
    )


def test_2125_direct_bus_re1_in_separate_stops():
    rows = collect([trip("2026-10-13T21:25:00+02:00",
                         "2026-10-13T22:30:00+02:00")])
    assert len(rows) == 1
    assert rows[0]["name"] == "Bus RE1"
    assert rows[0]["scheduled_departure"].startswith("2026-10-13T21:25")
    assert rows[0]["scheduled_arrival"].startswith("2026-10-13T22:30")
    assert rows[0]["explicit_sev"] is False
    assert rows[0]["origin"] == FROM and rows[0]["destination"] == TO


def test_next_day_bus_is_not_filtered_by_regional_train_clock():
    rows = collect([trip("2026-10-14T04:57:00+02:00",
                         "2026-10-14T06:02:00+02:00")])
    assert rows[0]["scheduled_departure"].startswith("2026-10-14T04:57")


def test_wrong_stop_or_transfer_is_not_a_direct_bus():
    row = trip("2026-10-13T21:25:00+02:00",
               "2026-10-13T22:30:00+02:00", origin="Leinefelde Bahnsteig 1")
    assert collect([row]) == []
    other = trip("2026-10-13T21:25:00+02:00",
                 "2026-10-13T22:30:00+02:00")
    other["legs"].append({"walking": True})
    assert collect([other]) == []


def test_rail_service_is_not_a_bus():
    train = trip("2026-10-13T21:25:00+02:00",
                 "2026-10-13T22:30:00+02:00", name="RE 1", mode="train")
    assert collect([train]) == []


def test_actual_sev_notice_can_confirm_not_generic_bus_name():
    row = trip("2026-10-13T21:25:00+02:00",
               "2026-10-13T22:30:00+02:00")
    row["legs"][0]["remarks"] = [{"text": "Schienenersatzverkehr"}]
    assert collect([row])[0]["explicit_sev"] is True


def test_source_partial_still_displays_positive_bus():
    rows = collect([trip("2026-10-13T21:25:00+02:00",
                         "2026-10-13T22:30:00+02:00")])
    report = bus.summarise_bus(rows, source_status="partial", errors=["reverse: HTTP 503"])
    assert report["service_count"] == 1
    assert report["explicit_sev_count"] == 0
