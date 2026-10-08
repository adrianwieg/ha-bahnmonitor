"""Regression tests for per-direction train history and conservative risk labels."""
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
PKG = "bahnmonitor_route_test"
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
health = load("route_health")
display = load("presentation")
TZ = ZoneInfo("Europe/Berlin")


def at(hour, minute):
    return datetime(2026, 10, 8, hour, minute, tzinfo=TZ)


def test_collect_in_both_directions_with_exact_route():
    now, target = at(15, 56), at(16, 9)
    own = [
        {"train": "RE RE1", "destination": "Göttingen", "scheduledDeparture": "14:23", "delayDeparture": 6},
        {"train": "RE RE11", "destination": "Göttingen", "scheduledDeparture": "15:22", "delayDeparture": 0},
        {"train": "RE RE1", "destination": "Nordhausen", "scheduledDeparture": "15:13", "delayDeparture": 14},
        {"train": "RE RE1", "destination": "Göttingen", "scheduledDeparture": "16:08", "delayDeparture": 12},
    ]
    other = [
        {"train": "RE RE11", "destination": "Leinefelde", "scheduledDeparture": "14:59", "delayDeparture": 9},
    ]
    a = health.collect_previous(own, direction="same", origin="Leinefelde", destination="Göttingen", planned=target, now=now)
    b = health.collect_previous(other, direction="reverse", origin="Göttingen", destination="Leinefelde", planned=target, now=now)
    assert len(a) == 2
    assert len(b) == 1
    result = health.summarise(a + b, target=target, now=now)
    assert result["status_code"] == "elevated"
    assert result["sample_count"] == 3
    assert result["same_direction"]["count"] == 2
    assert result["reverse_direction"]["count"] == 1
    assert result["delayed_count"] == 2
    assert result["vehicle_assignment_confirmed"] is False


def test_ambiguous_or_unobserved_trains_do_not_create_risk():
    now, target = at(15, 56), at(16, 9)
    board = [
        {"train": "RE 16243", "destination": "Göttingen", "scheduledDeparture": "15:08", "delayDeparture": 17},
        {"train": "RE RE1", "destination": "Göttingen", "scheduledDeparture": "15:22"},
        {"train": "RE RE11", "destination": "Göttingen", "scheduledDeparture": "16:02", "delayDeparture": 20},
    ]
    result = health.collect_previous(board, direction="same", origin="Leinefelde", destination="Göttingen", planned=target, now=now)
    assert result == []
    assert health.summarise(result, target=target, now=now)["status_code"] == "no_data"


def test_sparse_sample_not_reported_as_reliably_unaffected():
    row = {
        "direction": "reverse", "train": "RE RE11", "scheduled_departure": at(15, 30).isoformat(),
        "delay_minutes": 0, "cancelled": False, "observed_at": at(15, 40).isoformat(),
    }
    outcome = health.summarise([row], target=at(16, 9), now=at(15, 56))
    assert outcome["status_code"] == "limited"
    assert outcome["confidence"] == "limited"


def test_deduplication_prefers_recent_observation():
    row = {
        "direction": "same", "train": "RE RE1", "scheduled_departure": at(15, 30).isoformat(),
        "delay_minutes": 3, "cancelled": False, "observed_at": at(15, 35).isoformat(),
    }
    update = {**row, "delay_minutes": 12, "observed_at": at(15, 50).isoformat()}
    result = health.summarise([row, update], target=at(16, 9), now=at(15, 56))
    assert result["sample_count"] == 1
    assert result["average_delay_minutes"] == 12


def test_display_uses_german_and_correct_clock_times():
    trip = {
        "status": "delayed", "stale": False,
        "scheduled_departure": at(16, 9).isoformat(),
        "predicted_departure": at(16, 17).isoformat(),
        "departure_delay_minutes": 8,
        "platform": "4", "scheduled_platform": "2",
    }
    result = display.format_journey(trip, line="RE 1", origin="Göttingen", destination="Leinefelde")
    assert result["display_status"] == "Verspätet"
    assert result["display_delay"] == "+8 Min"
    assert result["display_departure"] == "16:17"
    assert result["platform_changed"] is True


def test_display_does_not_claim_unknown_train_is_confirmed():
    trip = {
        "status": "delayed", "line_match": "time_destination_unconfirmed",
        "scheduled_departure": at(16, 9).isoformat(), "stale": False,
    }
    result = display.format_journey(trip, line="RE 1", origin="Göttingen", destination="Leinefelde")
    assert result["display_status"] == "Zuordnung unbestätigt"


def test_morning_records_influence_1609_corridor_but_not_train_forecast():
    now = at(7, 52)
    target = at(16, 9)
    earlier = health.collect_previous(
        [
            {"train": "RE RE1", "destination": "Leinefelde",
             "scheduledDeparture": "07:20", "delayDeparture": 8},
            {"train": "RE RE11", "destination": "Leinefelde",
             "scheduledDeparture": "07:43", "delayDeparture": 0},
        ],
        direction="same", origin="Göttingen", destination="Leinefelde",
        planned=target, now=now,
    )
    later = health.collect_upcoming(
        [
            {"train": "RE RE11", "destination": "Göttingen",
             "scheduledDeparture": "08:09", "delayDeparture": 12},
        ],
        direction="reverse", origin="Leinefelde", destination="Göttingen",
        now=now,
    )
    assert len(earlier) == 2
    assert len(later) == 1
    result = health.summarise(earlier, target=target, now=now, upcoming=later)
    assert result["status"] == "Auffällig"
    assert result["same_direction"]["count"] == 2
    assert result["reverse_direction"]["current_departures"][0]["delay_minutes"] == 12
    assert result["monitoring_scope"] == "today_continuous"
    assert result["vehicle_assignment_confirmed"] is False


def test_yesterday_is_not_reused_for_today_or_tomorrow():
    now = at(7, 52)
    yesterday = {
        "direction": "same", "train": "RE RE1",
        "scheduled_departure": "2026-10-07T23:50:00+02:00",
        "delay_minutes": 45, "cancelled": False,
        "observed_at": "2026-10-07T23:55:00+02:00",
    }
    result = health.summarise(
        [yesterday], target=at(16, 9), now=now,
    )
    assert result["status_code"] == "no_data"
    assert result["sample_count"] == 0


def test_no_all_clear_on_forecast_only_with_no_delay():
    now, target = at(7, 52), at(16, 9)
    current = health.collect_upcoming(
        [{"train": "RE RE1", "destination": "Leinefelde",
          "scheduledDeparture": "08:05", "delayDeparture": 0}],
        direction="same", origin="Göttingen",
        destination="Leinefelde", now=now,
    )
    result = health.summarise([], target=target, now=now, upcoming=current)
    assert result["status_code"] == "limited"
    assert result["confidence"] == "limited"



def test_route_status_explains_which_trains_caused_warning():
    now = at(8, 10)
    target = at(16, 9)
    earlier = [
        {
            "direction": "reverse", "line": "RE 1", "train": "RE RE1",
            "scheduled_departure": at(7, 18).isoformat(),
            "delay_minutes": 23, "cancelled": False,
            "observed_at": now.isoformat(),
        },
        {
            "direction": "same", "line": "RE 1", "train": "RE RE1",
            "scheduled_departure": at(8, 9).isoformat(),
            "delay_minutes": 6, "cancelled": False,
            "observed_at": now.isoformat(),
        },
    ]
    forecast = [{
        "direction": "reverse", "line": "RE 1", "train": "RE RE1",
        "scheduled_departure": at(8, 43).isoformat(),
        "delay_minutes": 6, "cancelled": False,
        "observed_at": now.isoformat(),
    }]
    data = health.summarise(earlier, target=target, now=now, upcoming=forecast)
    assert data["status"] == "Auffällig"
    assert data["previous_delayed_count"] == 2
    assert data["forecast_delayed_count"] == 1
    assert data["average_delay_minutes"] == 14.5
    assert data["maximum_delay_minutes"] == 23
    assert "07:18 RE 1" in data["reason"]
    assert "08:09 RE 1" in data["reason"]
    assert "08:43 RE 1" in data["reason"]
    assert "Abfahrtsprognose" in data["reason"]
    assert len(data["trigger_reasons"]) == 3
