# Bahnmonitor – Home Assistant (v0.2.2, Prototyp)

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

## Klarere Anzeige vor der Abfahrt (v0.2.1)

Beispiel: RE 1 Göttingen → Leinefelde um 16:09 Uhr, Überprüfung um 07:52 Uhr.

- Seit **v0.2.2** läuft die **Streckenlage** ganztägig; die Meldung `Startet um 12:09` aus v0.2.1 entfällt.
- Der Sensor **Nächste Fahrt** zeigt `Zugprognose ab 12:09` und behält die konfigurierte Abfahrt 16:09 bei. Das Attribut `timetable_confirmed: false` macht klar, dass die Uhrzeit **noch nicht aus einer aktuellen Fahrplandatenquelle bestätigt** wurde.
- Der Sensor **Fahrplandienst** meldet `Teilweise verfügbar`, wenn IRIS die Streckenlage bereitstellt, aber die 7-Tage-Auskunft gestört ist.
- Die Dashboard-Karte beschriftet 16:09 vor einer erfolgreichen Datenabfrage als **konfigurierte Abfahrt** statt als offiziellen Fahrplan und zeigt die **ganztägige Streckenlage**.
- Der Fahrplandienst `v6.db.transport.rest` liefert weiterhin teilweise HTTP 503. Dies beeinträchtigt die kurzfristige IRIS-Streckenlage nicht, sofern IRIS erreichbar ist. Bei fehlenden Daten bitte Diagnose-JSON erneut prüfen.

## Ganztägige Streckenüberwachung (v0.2.2)

Die **Streckenlage ist jetzt sofort aktiv und unabhängig von der Abfahrtszeit deiner eigenen Fahrt**. Auch wenn dein RE 1 erst um 16:09 Uhr fährt, holt die Integration ab dem Start am Morgen die aktuellen RE-1- und RE-11-Abfahrten von **Göttingen und Leinefelde** ab.

- **Abruf alle 10 Minuten** über den bereits vorhandenen, gemeinsamen und rate-limitierten IRIS-Cache. Sowohl deine Richtung als auch die Gegenrichtung werden beobachtet.
- **Frühere Abfahrten von heute:** Die Integrationsdaten werden im Tagesverlauf gesammelt. Der Sensor enthält jeweils höchstens die **letzten drei geeigneten Züge pro Richtung** und deren gemeldete Verspätung beziehungsweise Ausfall. Sobald neue Züge erfasst werden, rücken die älteren aus der Anzeige.
- **Aktuelle Abfahrten:** Gemeldete Züge der **nächsten 45 Minuten** sind gesondert unter `current_departures` je Richtung sichtbar. Sie sind Prognosen, keine bestätigten tatsächlichen Abfahrten.
- **Bewertung:** `Unauffällig`, `Wenig Daten`, `Auffällig`, `Stark gestört` oder `Noch keine Beobachtungen`. Grundlage sind nur gemeldete Verspätungen/Ausfälle, keine pauschalen Annahmen über deinen eigenen Zug.
- **Tageshistorie persistent:** Beobachtungen werden verzögert in Home Assistants interner Integration-Storage-Datei gespeichert und beim Neustart wieder geladen. Die Auswertung berücksichtigt **nur den aktuellen Kalendertag**; Daten vom Vortag werden ausgeschlossen.
- **Unvollständige Daten:** Werden eine oder beide Stationstafeln nicht aktualisiert, wird die Abdeckung gekennzeichnet. Bei vollständigem Ausfall erscheint statt einer vermeintlich aktuellen Bewertung `Datenquelle gestört` beziehungsweise `Daten veraltet`.
- **Abdeckung:** Die externen Bahnhofstafeln liefern nur eine begrenzte Rückschau. Züge aus Zeiten, in denen Home Assistant noch nicht lief bzw. keine Daten erhalten hat, lassen sich nicht nachträglich vollständig rekonstruieren.
- **Eigener Zug:** Die separate, zugbezogene Echtzeitprüfung beginnt weiterhin **vier Stunden vor deiner konfigurierten Fahrt**. Die Streckenlage läuft unabhängig davon bereits vorher.

Die Streckenlage ist ein **Indikator über heutige Beobachtungen**, nicht die Prognose einer verbindlich feststehenden Folgeverspätung. Insbesondere beweist eine Verspätung des RE 11 nicht, dass der RE 1 um 16:09 Uhr verspätet sein wird.

## Neue Funktionen in v0.2.0

### Lesbare Fahrtanzeige

Der Sensor **Nächste Fahrt** zeigt nun deutschsprachige Zustände wie `Verspätet`, `Pünktlich`, `Planmäßig` oder `Fällt aus`. Als Attribute kommen `display_summary`, `display_departure`, `display_planned`, `display_delay`, `display_platform` und `platform_changed` hinzu. Die Rohdaten `journeys` und der maschinenlesbare `status_code` bleiben vorhanden. Bestehende Automationen, die auf den alten englischen Sensorzustand `delayed` reagieren, müssen ggf. auf `Verspätet` umgestellt werden.

### Streckenlage – bis zu drei frühere Züge pro Richtung

Der Sensor **Streckenlage** wertet die letzten erfassten **RE 1/RE 11** auf der konfigurierten Strecke und in der Gegenrichtung aus. Seit v0.2.2 gilt die ganztägige Überwachung aus dem Abschnitt oben, auch vor 12:09 Uhr für die 16:09-Uhr-Fahrt.

- Die Datenquelle **DBF/IRIS-TTS** liefert vergangene Zugabfahrten nur in einem **begrenzten Fenster von etwa 60 Minuten** (Option `past=1`), deshalb wird bei jedem Abruf eine Liste in Home Assistant **im Arbeitsspeicher** fortgeführt. Die Sammlung bleibt auf den aktuellen Kalendertag begrenzt.
- Pro Richtung zählen **höchstens drei verschiedene Züge**. Es werden nur genau zugeordnete RE 1 oder RE 11 und eine durch Ziel/Unterwegshalte nachweisbare Strecke berücksichtigt.
- **Keine Daten:** kein verwertbarer früherer Zug erfasst. **Wenig Daten:** nur einer. **Unauffällig:** mindestens zwei, ohne Verspätung ab 3 Minuten. **Auffällig:** mindestens ein Zug mit 3 oder mehr Minuten Verspätung. **Stark gestört:** mindestens ein gemeldeter Ausfall oder mindestens zwei Züge mit jeweils 10 oder mehr Minuten Verspätung.
- Die Einordnung ist **kein Verspätungsversprechen** für die eigene Fahrt. Eine planmäßig vergangene Abfahrt kann trotz Prognose noch unterwegs sein; die Daten belegen keine tatsächliche Fahrzeugdurchbindung. Bei unvollständigen oder fehlenden Daten wird keine Entwarnung behauptet.
- **Seit v0.2.2 wird die Tageshistorie gespeichert** und beim Neustart wieder geladen. Der Cache der externen API bleibt dagegen kurzfristig; fehlende historische Züge können nicht vollständig nachträglich beschafft werden.
- Unter **Einstellungen → Geräte & Dienste → Bahnmonitor → Konfigurieren** kann „Streckenlage aktivieren“ ein- oder ausgeschaltet werden; für bestehende Einträge ist es standardmäßig aktiv.

Die Attribute `same_direction` und `reverse_direction` enthalten Anzahl, Mittelwert, Verspätungen, Ausfälle und die jeweils jüngsten Züge. Der Sensor bleibt unabhängig davon, ob die siebentägige Fahrplanquelle einen 503-Fehler meldet.

### Lovelace-Karte (ohne zusätzliche HACS-Frontend-Plugins)

Die fertige Beispielkarte liegt in **[`examples/lovelace_card.yaml`](examples/lovelace_card.yaml)**.

1. Dashboard öffnen → **Bearbeiten** → **Karte hinzufügen → Manuell**.
2. Den Inhalt von `examples/lovelace_card.yaml` komplett in den YAML-Editor einfügen.
3. Die dort referenzierten Entity-IDs `sensor.pendlerzug_naechste_fahrt`, `sensor.pendlerzug_streckenlage`, `sensor.pendlerzug_fahrplandienst` und `binary_sensor.pendlerzug_zugausfall` auf die tatsächlich vergebenen IDs ändern, falls nötig.
4. Für weitere überwachte Züge kann die Karte kopiert und die Entity-IDs angepasst werden.

Die Karte kombiniert die Abfahrtszeit und Gleisinformation mit einem Ampeltext zur Streckenlage und Tabellen der vorherigen Züge.

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

Jede Verbindung erzeugt Sensoren `Nächste Fahrt`, `Streckenlage` und `Fahrplandienst` sowie Binärsensoren für **Zugausfall** und **Mögliche Folgeverspätung**. Home Assistant legt die tatsächlichen Entity-IDs fest.

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
- Die neue Streckenlagenfunktion ist ein **Prototyp** und muss mit echten Home-Assistant-/Fahrplandaten geprüft werden. Insbesondere Datenformat, Bahnhofserkennung und HACS-Kompatibilität können weitere Anpassungen erfordern.
