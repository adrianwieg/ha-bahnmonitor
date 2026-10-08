"""Fixtures based on the documented DBF/IRIS version-3 JSON field names.

These tests do not require Home Assistant or an external timetable service.
"""
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

path = Path(__file__).resolve().parents[1] / "custom_components/bahnmonitor/dbf.py"
spec = importlib.util.spec_from_file_location("bahn_dbf", path)
dbf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dbf)
BERLIN = ZoneInfo("Europe/Berlin")


def trip_time(hour=7, minute=30):
    return datetime(2026, 10, 8, hour, minute, tzinfo=BERLIN)


def test_direct_line_and_platform_delay():
    data = [{
        "train": "RE 11",
        "scheduledDeparture": "07:30",
        "delayDeparture": 8,
        "destination": "Leinefelde",
        "platform": "7",
        "scheduledPlatform": "5",
        "isCancelled": False,
    }]
    trip = dbf.board_departure(data, "RE11", "Leinefelde", trip_time(), 12)
    assert trip["line_match"] == "exact"
    assert trip["status"] == "delayed"
    assert trip["departure_delay_minutes"] == 8
    assert trip["platform"] == "7"
    assert trip["predicted_departure"] == "2026-10-08T07:38:00+02:00"


def test_re1_not_re11():
    data = [{"train": "RE 11", "scheduledDeparture": "07:30", "destination": "Göttingen"}]
    assert dbf.board_departure(data, "RE 1", "Göttingen", trip_time(), 12) is None


def test_operational_train_number_fallback_is_explicitly_unconfirmed():
    data = [{"train": "RE 16243", "scheduledDeparture": "07:31", "destination": "Göttingen"}]
    trip = dbf.board_departure(data, "RE 1", "Göttingen", trip_time(), 20)
    assert trip["line_match"] == "time_destination_unconfirmed"
    assert trip["line"] is None
    assert trip["observed_train"] == "RE 16243"


def test_ambiguous_regional_trains_are_not_guessed():
    data = [
        {"train": "RE 16243", "scheduledDeparture": "07:29", "destination": "Göttingen"},
        {"train": "RE 16333", "scheduledDeparture": "07:32", "destination": "Göttingen"},
    ]
    assert dbf.board_departure(data, "RE1", "Göttingen", trip_time(), 20) is None


def test_wrong_destination_ignored():
    data = [{"train": "RE 1", "scheduledDeparture": "07:30", "destination": "Erfurt"}]
    assert dbf.board_departure(data, "RE 1", "Göttingen", trip_time(), 12) is None


def test_incoming_turnaround_candidate():
    data = [{"train": "RE 11", "scheduledArrival": "07:15", "delayArrival": 18}]
    incoming = dbf.board_incoming(data, "RE 11", trip_time(), 30)
    assert incoming["plannedWhen"] == "2026-10-08T07:15:00+02:00"
    assert incoming["when"] == "2026-10-08T07:33:00+02:00"
    assert incoming["delay"] == 1080


def test_arrival_with_missing_realtime_is_unknown():
    data = [{"train": "RE 11", "scheduledArrival": "07:15"}]
    incoming = dbf.board_incoming(data, "RE 11", trip_time(), 30)
    assert incoming["when"] is None
