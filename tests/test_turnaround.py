"""Regression tests for route monitoring with automatic plausible terminal turns."""
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
PKG = "bahnmonitor_turn_test"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PKG, package)


def load(name):
    spec = spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("dbf")
turns = load("turnaround")
departures = load("route_departures")
load("disruptions")
debugger = load("debug")
now = datetime(2026, 10, 8, 15, 40, tzinfo=ZoneInfo("Europe/Berlin"))


def endpoint(arrivals=None, outgoing=None):
    return {
        "station": "Göttingen",
        "other": "Leinefelde",
        "arrivals": arrivals if arrivals is not None else [],
        "departures": outgoing if outgoing is not None else [],
    }


def arrival(*, end="Göttingen", delay=17, line="RE RE1"):
    return {
        "train": line,
        "destination": end,
        "route": ["Leinefelde", "Göttingen"],
        "scheduledArrival": "15:51",
        "delayArrival": delay,
    }


def departure(*, target="Leinefelde", line="RE RE1", when="16:09"):
    return {
        "train": line,
        "destination": target,
        "scheduledDeparture": when,
        "delayDeparture": None,
        "route": [target],
    }


def scan(arrivals=None, outgoing=None, lines=()):
    return turns.detect_turnarounds(
        now=now,
        endpoints=[endpoint(arrivals, outgoing)],
        lines=lines,
        min_turn_minutes=10,
        max_turn_minutes=45,
    )


def test_re1_gottingen_automatic_return_with_theoretical_delay():
    found = scan([arrival()], [departure()], ("RE 1",))
    assert found["status"] == "plausible"
    assert found["stations"] == ["Göttingen"]
    assert found["vehicle_assignment_confirmed"] is False
    one = found["candidates"][0]
    assert one["incoming_route"] == "Leinefelde → Göttingen"
    assert one["outgoing_route"] == "Göttingen → Leinefelde"
    assert one["arrival_scheduled"].startswith("2026-10-08T15:51")
    assert one["arrival_predicted"].startswith("2026-10-08T16:08")
    assert one["departure_scheduled"].startswith("2026-10-08T16:09")
    assert one["scheduled_turn_minutes"] == 18
    assert one["predicted_turn_minutes"] == 1
    assert one["potential_delay_minutes_if_same_vehicle"] == 9
    assert one["possible_followup_delay"] is True
    assert one["confidence"] == "corroborated"


def test_no_line_filter_still_detects_re1():
    found = scan([arrival()], [departure()])
    assert found["status"] == "plausible"
    assert found["candidates"][0]["line"] == "RE 1"


def test_intermediate_stop_cannot_prove_terminal_turn():
    result = scan([arrival(end="Nordhausen")], [departure()])
    assert result["status"] == "not_found"


def test_other_line_and_uncertain_run_number_not_mistaken_as_turn():
    result = scan([arrival(line="RE RE11")], [departure()])
    assert result["status"] == "not_found"
    assert turns.passenger_line({"train": "RE 16243"}) is None


def test_duplicate_return_departures_are_ambiguous():
    result = scan([arrival()], [departure(), departure(when="16:11")])
    assert result["status"] == "ambiguous"
    assert result["candidate_count"] == 2
    assert all(row["vehicle_assignment_confirmed"] is False for row in result["candidates"])
    assert all(row["possible_followup_delay"] is None for row in result["candidates"])


def test_no_delay_cannot_imply_followup_risk():
    result = scan([arrival(delay=None)], [departure()])
    row = result["candidates"][0]
    assert row["possible_followup_delay"] is None
    assert row["potential_delay_minutes_if_same_vehicle"] is None


def test_ordinary_punctual_departures_are_visible_both_directions():
    a = departures.collect_departures(
        [departure(), departure(line="RE RE11", when="16:11")],
        now=now, origin="Göttingen", destination="Leinefelde", direction="reverse",
        lines=("RE 1",),
    )
    assert len(a) == 1
    assert a[0]["status"] == "scheduled"
    assert a[0]["line"] == "RE 1"
    report = departures.summarise_departures(a, now=now, sources_ok=["reverse"])
    assert report["departure_count"] == 1
    assert report["source_status"] == "partial"


def test_cancelled_route_departure_explicit():
    board = [{**departure(), "isCancelled": True}]
    rows = departures.collect_departures(
        board, now=now, origin="Göttingen", destination="Leinefelde",
        direction="same",
    )
    assert rows[0]["status"] == "cancelled"


def test_missing_board_is_not_a_negative_turnaround_result():
    status = turns.detect_turnarounds(
        now=now, endpoints=[{"station": "Göttingen", "other": "Leinefelde",
                            "arrivals": None, "departures": []}],
    )
    assert status["status"] == "source_unavailable"
    assert status["source_status"] == "unavailable"


def test_arrival_at_other_endpoint_is_visible_with_delay():
    arrivals = departures.collect_arrivals(
        [arrival()],
        now=now, origin="Leinefelde", destination="Göttingen",
        direction="same", lines=("RE 1",),
    )
    assert len(arrivals) == 1
    assert arrivals[0]["planned_arrival"].startswith("2026-10-08T15:51")
    assert arrivals[0]["predicted_arrival"].startswith("2026-10-08T16:08")
    assert arrivals[0]["delay_minutes"] == 17
    timetable = departures.summarise_departures(
        [], now=now, sources_ok=["same", "reverse"], arrivals=arrivals,
    )
    assert timetable["next_arrival"] == arrivals[0]
    assert timetable["arrival_count"] == 1


def test_unrelated_incoming_service_does_not_belong_to_monitored_route():
    unrelated = {**arrival(), "route": ["Dresden", "Göttingen"], "origin": "Dresden"}
    arrivals = departures.collect_arrivals(
        [unrelated],
        now=now, origin="Leinefelde", destination="Göttingen",
        direction="same", lines=("RE 1",),
    )
    assert arrivals == []


def test_empty_live_boards_cannot_prove_absence_of_turnaround():
    report = scan([], [])
    assert report["status"] == "insufficient_data"
    assert report["candidate_count"] == 0
    assert report["relevant_services"] == 0
    assert report["source_status"] == "online"



def test_debug_shows_sev_at_completely_different_clock_time():
    """Replacement bus is observable without matching an RE departure time."""
    sample = [
        {"train": "RE RE1", "scheduledDeparture": "16:09",
         "destination": "Göttingen", "route": ["Göttingen"]},
        {"train": "Bus SEV RE1", "scheduledDeparture": "12:35",
         "destination": "Göttingen", "route": ["Göttingen"]},
    ]
    report = debugger.board_probe(
        sample, now=now, station="Leinefelde", other="Göttingen",
        direction="same", mode="dep", lines=("RE 1",),
    )
    assert report["raw_count"] == 2
    assert report["explicit_sev_count"] == 1
    sev_row = next(row for row in report["samples"] if row["explicit_sev"])
    assert sev_row["scheduled"] == "12:35"
    assert "ausserhalb_zeitfenster" in sev_row["excluded_by"]


def test_debug_identifies_partial_section_that_regular_filter_excludes():
    report = debugger.board_probe(
        [{
            "train": "Bus RE1", "scheduledDeparture": "16:50",
            "destination": "Eichenberg", "route": ["Eichenberg"],
        }],
        now=now, station="Leinefelde", other="Göttingen",
        direction="same", mode="dep", lines=("RE 1",),
    )
    assert report["raw_count"] == 1
    assert report["bus_label_count"] == 1
    assert report["explicit_sev_count"] == 0
    assert report["route_match_count"] == 0
    assert "ziel_oder_streckenabschnitt_passt_nicht" in report["samples"][0]["excluded_by"]


def test_debug_shows_actual_planned_bus_time_from_connection():
    api_response = {"journeys": [{
        "legs": [
            {"line": {"name": "Bus SEV RE1", "product": "bus"},
             "origin": {"name": "Leinefelde"},
             "destination": {"name": "Eichenberg"},
             "plannedDeparture": "2026-10-08T12:35:00+02:00",
             "departure": "2026-10-08T12:42:00+02:00"},
        ],
    }]}
    snapshot = debugger.journeys_probe(
        api_response, direction="same", requested_at=now,
    )
    leg = snapshot["journeys"][0]["legs"][0]
    assert leg["is_bus"] is True
    assert leg["explicit_sev"] is True
    assert leg["planned_departure"] == "2026-10-08T12:35:00+02:00"
