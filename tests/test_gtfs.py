"""Small synthetic GTFS fixtures for seven-day static timetable behaviour."""
from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from datetime import datetime
from zoneinfo import ZoneInfo

path = Path(__file__).resolve().parents[1] / "custom_components/bahnmonitor/gtfs.py"
spec = importlib.util.spec_from_file_location("bahn_gtfs", path)
gtfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gtfs)
TZ = ZoneInfo("Europe/Berlin")


def feed(*, duplicate=False, cancelled=False):
    routes = "route_id,route_short_name\nr1,RE 1\nr11,RE 11\nr2,RE 10\n"
    trips = "route_id,service_id,trip_id\nr1,weekday,t1\nr11,weekday,t2\nr2,weekday,t3\n"
    times = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "t1,16:09:00,16:09:00,goe-p1,1\n"
        "t1,16:42:00,16:43:00,lei-p1,2\n"
        "t2,15:15:00,15:15:00,lei-p1,1\n"
        "t2,15:49:00,15:50:00,goe-p1,2\n"
        "t3,16:09:00,16:09:00,goe-p1,1\n"
        "t3,16:42:00,16:42:00,lei-p1,2\n"
    )
    if duplicate:
        trips += "r1,weekday,t4\n"
        times += "t4,16:10:00,16:10:00,goe-p1,1\nt4,16:43:00,16:43:00,lei-p1,2\n"
    stops = (
        "stop_id,stop_name,parent_station\n"
        "goe,Göttingen,\n"
        "lei,Leinefelde,\n"
        "goe-p1,Göttingen,goe\n"
        "lei-p1,Leinefelde,lei\n"
    )
    calendar = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "weekday,1,1,1,1,1,0,0,20261001,20261101\n"
    )
    exceptions = "service_id,date,exception_type\n"
    if cancelled:
        exceptions += "weekday,20261008,2\n"
    payloads = {
        "routes.txt": routes,
        "trips.txt": trips,
        "stop_times.txt": times,
        "stops.txt": stops,
        "calendar.txt": calendar,
        "calendar_dates.txt": exceptions,
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for filename, data in payloads.items():
            archive.writestr(filename, data)
    return buffer.getvalue()


def planned():
    return datetime(2026, 10, 8, 16, 9, tzinfo=TZ)


def test_gtfs_unique_service_on_valid_date():
    parsed = gtfs._parse_gtfs(feed())
    found = gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
    )
    assert found["status"] == "scheduled"
    assert found["realtime_confirmed"] is False
    assert found["scheduled_departure"] == "2026-10-08T16:09:00+02:00"
    assert found["scheduled_arrival"] == "2026-10-08T16:42:00+02:00"
    assert "CC BY 4.0" in found["source_attribution"]


def test_gtfs_does_not_confuse_re1_with_re10_or_re11():
    parsed = gtfs._parse_gtfs(feed())
    assert gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 11", planned=planned(), tolerance=15,
    ) is None


def test_gtfs_missing_service_date_is_not_cancelled():
    parsed = gtfs._parse_gtfs(feed(cancelled=True))
    assert gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
    ) is None


def test_gtfs_ambiguous_train_does_not_guess():
    parsed = gtfs._parse_gtfs(feed(duplicate=True))
    assert gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
    ) is None


def test_gtfs_rejects_bad_zip():
    try:
        gtfs._parse_gtfs(b"not a ZIP file")
    except gtfs.GtfsError:
        pass
    else:
        assert False, "Bad archive must not become an empty but valid timetable"


def test_match_diagnostic_explains_success():
    parsed = gtfs._parse_gtfs(feed())
    explanation = {}
    trip = gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
        diagnostic=explanation,
    )
    assert trip is not None
    assert explanation["reason"] == "matched"
    assert explanation["time_candidates"] == 1


def test_match_diagnostic_explains_calendar_exception():
    parsed = gtfs._parse_gtfs(feed(cancelled=True))
    explanation = {}
    trip = gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
        diagnostic=explanation,
    )
    assert trip is None
    assert explanation["reason"] == "no_active_calendar_service"
    assert explanation["time_candidates"] == 0


def test_match_diagnostic_explains_ambiguous_times():
    parsed = gtfs._parse_gtfs(feed(duplicate=True))
    explanation = {}
    trip = gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1", planned=planned(), tolerance=15,
        diagnostic=explanation,
    )
    assert trip is None
    assert explanation["reason"] == "ambiguous_multiple_departures"
    assert explanation["time_candidates"] == 2


def test_match_diagnostic_shows_nearest_timetable_times():
    parsed = gtfs._parse_gtfs(feed())
    explanation = {}
    trip = gtfs.find_trip(
        parsed, origin="Göttingen", destination="Leinefelde",
        line="RE 1",
        planned=datetime(2026, 10, 8, 17, 45, tzinfo=TZ),
        tolerance=10, diagnostic=explanation,
    )
    assert trip is None
    assert explanation["reason"] == "no_departure_within_search_window"
    assert "2026-10-08T16:09:00+02:00" in explanation["nearest_planned_departures"]


def test_missing_direct_trip_reports_origin_only_service():
    """An RE departure at Göttingen may terminate short of Leinefelde."""
    data = gtfs._parse_gtfs(feed())
    debug = {}
    result = gtfs.find_trip(
        data, origin="Göttingen", destination="Eichenberg",
        line="RE 1", planned=planned(), tolerance=10,
        diagnostic=debug,
    )
    assert result is None
    assert debug["reason"] == "no_matching_direct_route"
    assert debug["origin_service_count"] >= 1
    assert debug["nearest_origin_departures"][0] == {
        "time": "2026-10-08T16:09:00+02:00",
        "last_stop": "Leinefelde",
    }


def test_no_match_diagnostic_remains_unconfirmed():
    data = gtfs._parse_gtfs(feed())
    debug = {}
    result = gtfs.find_trip(
        data, origin="Göttingen", destination="Leinefelde",
        line="RE 1",
        planned=datetime(2026, 10, 14, 17, 45, tzinfo=TZ),
        tolerance=10, diagnostic=debug,
    )
    assert result is None
    assert debug["reason"] == "no_departure_within_search_window"
    assert debug["time_candidates"] == 0
