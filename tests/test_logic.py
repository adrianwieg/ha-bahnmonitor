"""Standalone logic checks; Home Assistant installation not required."""
import importlib.util
from datetime import datetime
from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'custom_components/bahnmonitor/logic.py'
spec = importlib.util.spec_from_file_location('bahnlogic', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_line_match():
    assert m.normalize_line('RE 11') == m.normalize_line('RE11')
    assert m.normalize_line('RE 1') != m.normalize_line('RE 11')


def test_inbound_risk_is_unconfirmed():
    out = datetime.fromisoformat('2026-10-08T17:00:00+02:00')
    arrivals = [{'line': {'name': 'RE 11'}, 'plannedWhen': '2026-10-08T16:45:00+02:00', 'when': '2026-10-08T17:03:00+02:00', 'delay': 1080}]
    found = m.select_incoming(arrivals, 'RE11', out, 45)
    assert found is not None
    risk = m.evaluate_turnaround(found, out, 8)
    assert risk['risk'] and risk['confirmed_vehicle'] is False


def test_absent_is_not_cancelled():
    assert not m.cancellation({})
    assert m.evaluate_turnaround(None, datetime.fromisoformat('2026-10-08T17:00:00+02:00'), 8)['status'] == 'unknown'
