"""General route alerts: SEV is explicit evidence, never inferred from a bus."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "custom_components" / "bahnmonitor"
spec = spec_from_file_location("bahnmonitor_alerts_test", HERE / "disruptions.py")
alerts = module_from_spec(spec)
spec.loader.exec_module(alerts)


def extract(legs, remarks=None):
    return alerts.extract_route_alerts(
        {"journeys": [{"legs": legs, "remarks": remarks or []}]},
        direction="same", origin="Leinefelde", destination="Göttingen",
    )


def test_explicit_sev_from_rail_remarks():
    result = extract([{
        "line": {"name": "RE 1"},
        "remarks": [{"type": "warning", "text":
                     "Schienenersatzverkehr zwischen Leinefelde und Göttingen."}],
    }])
    assert result["sev_reported"] is True
    assert result["incidents"][0]["type"] == "sev"
    assert result["incidents"][0]["line"] == "RE 1"
    merged = alerts.merge_route_alerts(
        {"status": "Noch keine Beobachtungen", "status_code": "no_data",
         "summary": "Wenig Daten", "sample_count": 0},
        {**result, "source_status": "online"},
    )
    assert merged["status"] == "Schienenersatzverkehr gemeldet"
    assert merged["status_code"] == "sev"
    assert "Schienenersatzverkehr" in merged["reason"]


def test_named_replacement_bus_is_evidence():
    result = extract([{"line": {"name": "Bus SEV"}, "product": "bus"}])
    assert result["sev_reported"]
    assert result["incidents"][0]["type"] == "sev"


def test_normal_bus_transfer_does_not_prove_sev():
    result = extract([
        {"line": {"name": "Bus 61"}, "product": "bus"},
        {"line": {"name": "RE 2"}, "product": "regionalExpress"},
    ])
    assert result["incidents"] == []
    assert result["sev_reported"] is False
    merged = alerts.merge_route_alerts(
        {"status": "Wenig Daten", "status_code": "limited"},
        {**result, "source_status": "online", "journey_count": 1},
    )
    assert merged["status"] == "Wenig Daten"
    assert "keine Bestätigung" in merged["alert_note"]


def test_construction_cancellation_and_deduplication():
    result = extract([
        {"line": {"name": "RB 40"}, "cancelled": True,
         "remarks": [
             {"text": "Bauarbeiten zwischen zwei Stationen"},
             {"text": "Bauarbeiten zwischen zwei Stationen"},
         ]},
    ])
    assert {item["type"] for item in result["incidents"]} == {
        "construction", "cancellation",
    }
    assert len(result["incidents"]) == 2


def test_unavailable_source_does_not_reuse_old_incident():
    merged = alerts.merge_route_alerts(
        {"status": "Datenquelle gestört", "status_code": "source_unavailable"},
        {"source_status": "unavailable", "incidents": [],
         "sev_reported": False},
    )
    assert merged["status_code"] == "source_unavailable"
    assert merged["sev_reported"] is False


def test_cannot_explain_cause_by_non_disruption_remark():
    assert alerts.classify("WLAN und Fahrradmitnahme verfügbar") is None
    assert alerts.classify("Kein Schienenersatzverkehr erforderlich") is None
    assert alerts.classify("Vollsperrung wegen Bauarbeiten") == "closure"


def test_iris_explicit_sev_without_connections_provider():
    events = alerts.extract_board_alerts(
        [{"train": "Bus SEV", "destination": "Göttingen",
          "scheduledDeparture": "18:30", "route": ["Göttingen"]}],
        direction="same", origin="Leinefelde", destination="Göttingen",
    )
    assert len(events) == 1
    assert events[0]["type"] == "sev"
    assert events[0]["source"] == "DBF/IRIS-TTS"
    merged = alerts.merge_route_alerts(
        {"status": "Noch keine Beobachtungen", "status_code": "no_data",
         "summary": "Wenig Daten", "sample_count": 0,
         "source_status": "online", "board_incidents": events},
        {"source_status": "unavailable", "errors": ["HTTP 503"],
         "incidents": [], "journey_count": 0},
    )
    assert merged["status"] == "Schienenersatzverkehr gemeldet"
    assert merged["sev_reported"] is True
    assert merged["alert_source_status"] == "unavailable"
    assert merged["alert_sources"]["station_boards"] == "online"
    assert merged["alert_board_count"] == 1


def test_no_false_sev_from_a_normal_bus_or_wrong_route():
    board = [
        {"train": "Bus 61", "destination": "Göttingen"},
        {"train": "Bus SEV", "destination": "Nordhausen"},
        {"train": "RE RE1", "destination": "Göttingen",
         "messages": {"qos": [{"text": "Klimaanlage defekt"}]}},
    ]
    assert alerts.extract_board_alerts(
        board, direction="same", origin="Leinefelde", destination="Göttingen",
    ) == []


def test_iris_line_filter_does_not_attribute_unrelated_sev():
    board = [
        {"train": "Bus SEV", "destination": "Göttingen"},
        {"train": "RE RE1", "destination": "Göttingen",
         "messages": {"delay": [{"text": "Schienenersatzverkehr im Abschnitt"}]}},
        {"train": "RE RE11", "destination": "Göttingen",
         "messages": {"delay": [{"text": "Umleitung wegen Bauarbeiten"}]}},
    ]
    events = alerts.extract_board_alerts(
        board, direction="same", origin="Leinefelde", destination="Göttingen",
        lines=("RE 1",),
    )
    assert len(events) == 1 and events[0]["type"] == "sev"


def test_connection_outage_with_no_iris_evidence_is_not_all_clear():
    merged = alerts.merge_route_alerts(
        {"status": "Noch keine Beobachtungen", "status_code": "no_data",
         "summary": "Keine Züge", "sample_count": 0,
         "source_status": "online", "board_incidents": []},
        {"source_status": "unavailable", "errors": ["HTTP 503"],
         "incidents": [], "journey_count": 0},
    )
    assert merged["status"] == "Daten unvollständig"
    assert merged["status_code"] == "partial_source_unavailable"
    assert merged["incidents"] == []
    assert merged["sev_reported"] is False
    assert "keine bestätigte entwarnung" in merged["alert_note"].lower()



def test_partial_bus_route_is_visible_but_never_confirmed_sev():
    route = {
        "status": "Noch keine Beobachtungen",
        "status_code": "no_data",
        "summary": "Keine passenden Direktfahrten",
        "sample_count": 0,
        "source_status": "online",
        "corridor": {
            "candidate_count": 0,
            "candidates": [],
            "observed_section_count": 1,
            "observed_bus_sections": [
                {
                    "train": "Bus RE8",
                    "interchange": "Eichenberg",
                    "confirmed_sev": False,
                },
            ],
        },
    }
    result = alerts.merge_route_alerts(
        route,
        {"source_status": "unavailable",
         "incidents": [], "journey_count": 0, "errors": ["HTTP 503"]},
    )
    assert result["status_code"] == "partial_bus_unconfirmed"
    assert "Bus-Teilstrecke" in result["status"]
    assert result["sev_reported"] is False
    assert result["incidents"] == []
    assert result["alert_source_status"] == "unavailable"
    assert "Eichenberg" in result["summary"]
    assert "unbestätigt" in result["alert_note"].lower()
