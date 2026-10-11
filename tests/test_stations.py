"""Check offline station selection without Home Assistant or network."""
import importlib.util
from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'custom_components/bahnmonitor/stations.py'
spec = importlib.util.spec_from_file_location('bahn_stations', p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_primary_stations():
    assert module.known_station('Göttingen') == ('8000128', 'Göttingen')
    assert module.known_station('Leinefelde') == ('8010203', 'Leinefelde')


def test_aliases_and_spaces():
    assert module.known_station('Goettingen Hbf') == ('8000128', 'Göttingen')
    assert module.known_station('  Bahnhof   Leinefelde   ') == ('8010203', 'Leinefelde')


def test_other_stations_require_live_lookup():
    assert module.known_station('Berlin Hbf') is None
    assert module.known_station('Göttingen Geismar') is None


def test_static_station_directory_exact_match():
    stations = {
        "8000105": {"type": "station", "id": "8000105", "name": "Frankfurt (Main) Hbf"},
        "8000106": {"type": "station", "id": "8000106", "name": "Frankfurt (Main) Süd"},
    }
    assert module.select_station(stations, "Frankfurt (Main) Hbf") == (
        "8000105", "Frankfurt (Main) Hbf"
    )
    assert module.select_station(stations, "Frankfurt (Main) Hauptbahnhof") == (
        "8000105", "Frankfurt (Main) Hbf"
    )
    assert module.select_station(stations, "Frankfurt (Main)") is None


def test_live_locations_match_does_not_select_wrong_suburb():
    results = [
        {"type": "stop", "id": "1234567", "name": "Berlin Gesundbrunnen"},
        {"type": "stop", "id": "8011160", "name": "Berlin Hbf"},
    ]
    assert module.select_station(results, "Berlin Hbf") == ("8011160", "Berlin Hbf")
    assert module.select_station(results, "Berlin") is None


def test_ambiguous_same_station_name_requires_clarification():
    options = [
        {"id": "1000000", "name": "Neustadt", "type": "station"},
        {"id": "2000000", "name": "Neustadt", "type": "station"},
    ]
    assert module.select_station(options, "Neustadt") is None
