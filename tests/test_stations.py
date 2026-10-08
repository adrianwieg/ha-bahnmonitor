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
