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


def test_iris_train_format_from_real_diagnostics():
    """Real station-board sample: line RE 1 at 07:18 toward Göttingen."""
    board = [
        {"train": "ABR RB57", "destination": "Nordhausen", "scheduledDeparture": "07:17"},
        {"train": "RB RB52", "destination": "Erfurt Hbf", "scheduledDeparture": "07:18"},
        {"train": "RE RE1", "line": "RE", "destination": "Göttingen", "scheduledDeparture": "07:18"},
        {"train": "RE RE11", "destination": "Neudietendorf", "scheduledDeparture": "07:40"},
        {"train": "ABR RE8", "destination": "Eichenberg", "scheduledDeparture": "07:53"},
    ]
    result = dbf.board_departure(
        board, "RE 1", "Göttingen", trip_time(7, 18), 20
    )
    assert result is not None
    assert result["line_match"] == "exact"
    assert result["line"] == "RE 1"
    assert result["observed_train"] == "RE RE1"
    assert result["scheduled_departure"] == "2026-10-08T07:18:00+02:00"
    assert result["source"] == "DBF/IRIS-TTS"
    assert result["status"] == "scheduled"
    assert dbf.board_departure(
        board, "RE 11", "Göttingen", trip_time(7, 18), 20
    ) is None


def test_iris_operational_prefix_exactness():
    assert dbf.matches_line("RE RE1", "RE 1")
    assert dbf.matches_line("RE RE11", "RE 11")
    assert dbf.matches_line("RE RE 1", "RE1")
    assert dbf.matches_line("ABR RE8", "RE 8")
    assert not dbf.matches_line("RE RE11", "RE1")
    assert not dbf.matches_line("RE RE1", "RE 11")
    assert not dbf.matches_line("RE 16243", "RE 1")
    assert not dbf.matches_line("IRE1", "RE 1")
    assert not dbf.matches_line("RE RE1 (RE 11)", "RE 1")


def test_iris_incoming_prefix():
    board = [{
        "train": "RE RE1",
        "scheduledArrival": "07:05",
        "delayArrival": 9,
    }]
    incoming = dbf.board_incoming(board, "RE1", trip_time(7, 18), 30)
    assert incoming["plannedWhen"] == "2026-10-08T07:05:00+02:00"
    assert incoming["when"] == "2026-10-08T07:14:00+02:00"



def test_multiple_incoming_services_are_not_unique_vehicle_evidence():
    board = [
        {"train": "RE RE1", "scheduledArrival": "16:00", "delayArrival": 4},
        {"train": "RE RE1", "scheduledArrival": "15:48", "delayArrival": 12},
        {"train": "RE RE11", "scheduledArrival": "15:52", "delayArrival": 7},
    ]
    result = dbf.incoming_candidates(
        board, "RE 1", trip_time(16, 9), 30
    )
    assert len(result) == 2
    assert all(item["line"]["name"] == "RE 1" for item in result)
    assert result[0]["plannedWhen"].endswith("16:00:00+02:00")


def test_single_incoming_candidate_does_not_prove_vehicle_link():
    board = [
        {"train": "RE RE11", "scheduledArrival": "15:52", "delayArrival": 7},
    ]
    result = dbf.incoming_candidates(
        board, "RE 11", trip_time(16, 9), 30
    )
    assert len(result) == 1
    assert result[0]["delay"] == 420
    assert result[0]["observed_train"] == "RE RE11"
