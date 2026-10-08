# Bahnmonitor – Home Assistant (v0.1.6, Prototyp)

Überwacht wiederkehrende Verbindungen (z. B. RE 1 und RE 11 zwischen Leinefelde und Göttingen) bis zu sieben Tage im Voraus. Jede Fahrt wird als eigener GUI-Eintrag angelegt. Für Fahrten ab Göttingen kann eine **mögliche** Folgeverspätung aus einem ankommenden Zug derselben Linie abgeleitet werden. Die physische Fahrzeugdurchbindung wird **nicht** nachgewiesen.

## Installation über HACS (empfohlen)

1. Das GitHub-Repository muss für die HACS-Installation öffentlich erreichbar sein.
2. In Home Assistant **HACS** öffnen, rechts oben **⋮ → Benutzerdefinierte Repositories**.
3. Repository-URL `https://github.com/adrianwieg/ha-bahnmonitor` eintragen und die Kategorie **Integration** wählen. Hinzufügen.
4. In HACS nach **Bahnmonitor** suchen und die Integration herunterladen/installieren.
5. **Home Assistant vollständig neu starten**.
6. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → Bahnmonitor** die gewünschten Fahrten konfigurieren.

**Hinweis zum temporären Public-Status:** Wenn das GitHub-Repository wieder privat gestellt wird, kann HACS ohne separaten Zugriff die Updates nicht mehr abrufen. Die bereits heruntergeladenen Integrationsdateien werden dadurch nicht automatisch gelöscht. Für Updates muss das Repository erreichbar bleiben oder erneut öffentlich werden.

## Manuelle Installation

Den Ordner `custom_components/bahnmonitor` nach `/config/custom_components/bahnmonitor` kopieren und Home Assistant neu starten.

## Einrichtung

**Bahnhofssuche:** Göttingen (8000128) und Leinefelde (8010203) werden ab v0.1.2 direkt über hinterlegte Bahnhof-IDs erkannt. Dadurch ist die Einrichtung dieser Strecke auch bei Störungen der Online-Bahnhofssuche möglich. Für die eigentlichen Fahrplan- und Echtzeitdaten ist die API weiterhin notwendig. Für andere Bahnhöfe bleibt die Online-Suche aktiviert.


- Ein Verbindungseintrag = eine Zugfahrt mit Uhrzeit und Verkehrstagen, z. B. drei Einträge für drei täglich genutzte Züge.
- Linie: `RE 1` oder `RE 11` (andere Linien grundsätzlich möglich).
- Wochentage: Montag=0 bis Sonntag=6, beispielsweise `0,1,2,3,4` für Montag bis Freitag.
- Abfahrt im Format `HH:MM`.
- Vorleistungsprüfung bei Abfahrt ab Göttingen optional aktivieren.
- Einträge über **Konfigurieren** nachträglich bearbeiten.

## Datenquellen ab Version 0.1.4

**DB Infoscreen / IRIS-TTS:** Für Fahrten von etwa 30 Minuten vor bis 4 Stunden nach der aktuellen Uhrzeit nutzt Bahnmonitor zuerst die DBF-Stationstafel (öffentlicher Endpoint `https://dbf.finalrewind.org/<EVA>.json?version=3`). Dies ist der Datenweg der etablierten Home-Assistant-Integration [DB Infoscreen](https://github.com/FaserF/ha-db_infoscreen), aber Bahnmonitor ist eine eigenständige Integration. Der öffentliche DBF-Dienst soll **höchstens einmal pro Minute je Station** abgefragt werden. Daher teilen sich alle konfigurierten Bahnmonitor-Fahrten einen Cache; bei Bedarf wird die Ankunftsüberwachung später erneut versucht.

**db.transport.rest:** Für Verbindungen, die weiter in der Zukunft liegen, bleibt der bisherige Fahrplandienst als Best-Effort-Abfrage bestehen. Er liefert teilweise HTTP 503; in diesem Fall erfolgt ein gestaffelter Wiederholungsversuch nach 15/30/60/maximal 120 Minuten. Ein Ausfall dieser Quelle blockiert aktuelle, erfolgreich von DBF abgefragte Fahrten nicht mehr.

**Grenzen:** Eine IRIS-Abfahrtstafel bietet keine verlässliche 7-Tage-Vorschau. Für Tage ohne Daten steht `unknown` bzw. `not_found` und **nicht** „pünktlich“. Die Statusinformationen `provider_status=partial` und `source=DBF/IRIS-TTS` zeigen an, wenn die Echtzeitquelle arbeitet, aber weit entfernte Abfragen fehlen. Falls IRIS nur betriebliche Zugnummern wie `RE 16243` statt `RE 1` ausliefert, wird ein eindeutiger Zeit-/Richtungsabgleich mit `line_match=time_destination_unconfirmed` markiert. Diese Heuristik ist keine sichere Linienzuordnung; entsprechende Zugausfälle werden nicht als bestätigt gemeldet.

**Göttingen als Wendebahnhof:** Ankünfte derselben Linie können ein Risiko für den nächsten Umlauf andeuten. IRIS weist damit jedoch keine Fahrzeugdurchbindung nach. Falls die separate Ankunftstafel durch den öffentlichen Abrufabstand nicht verfügbar ist, bleibt die Vorleistungsprognose unbekannt.

## Ausfallsicherheit

Bei Ausfall der Fern-Fahrplandaten bleibt die Integration geladen; Fahrten mit aktuellen IRIS-Daten bleiben nutzbar. Der Sensor **Fahrplandienst** meldet `partial`, wenn nur ein Teil der sieben Tage abgedeckt ist, und `unavailable`, wenn keine frischen Daten verfügbar sind. Vorhandene Werte werden bei nicht erfolgreicher Aktualisierung als `stale` gekennzeichnet. Binärsensoren werden bei fehlender oder nicht zuordenbarer Datenlage nicht fälschlich als Entwarnung ausgegeben.

## Fehlerbehebung in v0.1.6

IRIS meldet die Linie **RE 1** unter Umständen als `RE RE1` und die Linie **RE 11** als `RE RE11` (Produktklasse plus Linienkennung). Die frühere Erkennung behandelte diesen doppelten Präfix als unbekannte Linie. Ab v0.1.6 erkennt der Parser diese Namen als exakte Linienübereinstimmung und verwechselt RE1 und RE11 nicht. Auch die Prüfung einer möglichen Vorleistung in Göttingen profitiert von der Korrektur.

Dies behebt den in den Home-Assistant-Diagnosedaten vom 08.10.2026 um 07:14 beobachteten Fehler: Die IRIS-Antwort enthielt einen RE RE1 um 07:18 ab Leinefelde nach Göttingen, der nicht zugeordnet wurde. Ein weiterhin auftretender HTTP 503 beim separaten 7-Tage-Anbieter bleibt unabhängig davon bestehen.

## Debugging und Diagnose ab v0.1.5

Falls die Entitäten angelegt wurden, aber die Fahrplandaten fehlen:

1. In Home Assistant **Einstellungen → Geräte & Dienste → Bahnmonitor** öffnen.
2. Beim Eintrag über **⋮ → Diagnose herunterladen** die Diagnose-JSON exportieren.
3. Alternativ unter **Entwicklerwerkzeuge → Zustände** den Sensor **Fahrplandienst** auswählen und dessen Attribut `diagnostics` ansehen.
4. Besonders hilfreich: `realtime_dbf.status`, `realtime_dbf.error`, `realtime_dbf.returned_count`, `realtime_dbf.sample`, `future_timetable.last_error`, `next_scheduled_departure` und `retry_at`.

Eine Abfrage der DBF-Echtzeitdaten wird nur durchgeführt, wenn die konfigurierte Abfahrt frühestens 30 Minuten vergangen oder höchstens vier Stunden entfernt ist. Bei einer bereits länger zurückliegenden Abfahrt ist `realtime_dbf.status=skipped` erwartetes Verhalten und **kein neuer Fehler**. Beim nächsten passenden Abfahrtstermin wird sie erneut geprüft.

Für detaillierte Home-Assistant-Protokolle zusätzlich die folgende Einstellung unter `configuration.yaml` verwenden (bestehenden `logger:`-Block ergänzen, **nicht doppelt anlegen**):

```yaml
logger:
  logs:
    custom_components.bahnmonitor: debug
```

Nach einem vollständigen Neustart unter **Einstellungen → System → Protokolle** auf `bahnmonitor` filtern. Die Protokolle enthalten keine Tokens oder Zugangsdaten; vor dem Weitergeben von Diagnose-JSON kann man optional Fahrzeiten und Stationsnamen entfernen.

Der Diagnosesensor **Fahrplandienst** verwendet die menschenlesbaren Zustände `Online`, `Teilweise verfügbar`, `Gestört` und `Noch nicht geprüft`; der maschinenlesbare Wert `provider_status` bleibt unverändert verfügbar.

## Sensoren und Automationen

Jede Verbindung erzeugt einen Sensor `Nächste Fahrt` mit Attribut `journeys` für die kommenden sieben Tage sowie Binärsensoren für **Zugausfall** und **Mögliche Folgeverspätung**. Home Assistant legt die tatsächlichen Entity-IDs fest.

```yaml
alias: Bahnmonitor Warnung
triggers:
  - trigger: state
    entity_id: binary_sensor.pendlerzug_mogliche_folgeverspatung
    to: "on"
actions:
  - action: notify.mobile_app_mein_iphone
    data:
      title: "Bahnmonitor: Mögliche Folgeverspätung"
      message: "Eine mögliche Vorleistung erreicht Göttingen verspätet. Fahrzeugdurchbindung nicht bestätigt."
mode: single
```

Bitte die Entity-ID und den mobilen Benachrichtigungsdienst anpassen.

## Datenqualität und Grenzen

- Öffentliche Drittanbieter-Schnittstelle `v6.db.transport.rest`, ohne API-Schlüssel oder zugesicherten Dienst.
- 7-Tage-Anzeigen nur soweit die Quelle entsprechende Fahrpläne liefert; längerfristig nicht gleichbedeutend mit Echtzeitprognose.
- Stündliche Aktualisierung weiter entfernter Fahrten, näher an der Abfahrt alle zehn Minuten.
- `not_found` bedeutet **nicht** einen bestätigten Ausfall.
- Vorleistungsprüfung: gleiche Linie und passende Ankunftszeit, **keine bestätigte Fahrzeuginformation**.
- Die Integration ist ein **ungetesteter Prototyp** und muss mit echten Home-Assistant-/Fahrplandaten geprüft werden. Insbesondere Datenformat, Bahnhofserkennung und HACS-Kompatibilität können weitere Anpassungen erfordern.
