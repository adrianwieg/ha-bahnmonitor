# Bahnmonitor – Home Assistant

Custom Integration zur Überwachung wiederkehrender Zugfahrten und der Streckenlage. Entwickelt für **RE 1 und RE 11 zwischen Göttingen und Leinefelde**, mit frei konfigurierbaren Start- und Zielbahnhöfen.

**Version 0.3.5 – Prototyp**. Installation über HACS möglich; API- und Fahrplandaten müssen im eigenen Home Assistant praktisch geprüft werden.

## Veröffentlichte Verspätungsgründe für RE 1/RE 11 (v0.3.5)

Die bereits für die Streckenlage verwendete DBF-/IRIS-Stationstafel enthält neben `delayDeparture` auch **`messages.delay`**, sofern für den konkreten Zug eine offizielle Verspätungsmeldung veröffentlicht wurde. Die Integration übernimmt diese Meldungen **ohne weitere API-Anfragen** und speichert sie gemeinsam mit den täglichen Streckenbeobachtungen. Es werden höchstens drei verschiedene Meldungen pro Zug samt Quellzeitstempel gespeichert; redundante Nachrichten werden entfernt.

- In `same_direction.trains`, `reverse_direction.trains`, `same_direction.current_departures` und `reverse_direction.current_departures` stehen die Felder **`delay_reason`** (erste Meldung oder `null`) und **`delay_reasons`** (Liste von Objekten mit `text`, `timestamp`, `source`).
- Der Streckenindikator führt im Feld `reason` und der Liste `trigger_reasons` veröffentlichte Ursachen an, ohne aus der Verspätungsdauer eigene Gründe zu erfinden. Die Zahl der aktuell mit veröffentlichten Ursachen versehenen verspäteten Fahrten steht in `published_delay_reason_count`.
- Der Zugstatus der eigenen Fahrt (`journeys[0]`) hat bei erfolgreicher IRIS-Abfrage ebenfalls `delay_reason` und `delay_reasons`. Für die mögliche Vorleistung gibt es außerdem `incoming_departure_delay_reasons` und `incoming_arrival_delay_reasons`.
- **`messages.qos` sind andere Zugmeldungen**, beispielsweise Ausstattungs- und Qualitätsinformationen, und werden ausdrücklich **nicht** als Verspätungsursache angezeigt.
- Bei einem verspäteten Zug ohne `messages.delay` steht im Dashboard **„Kein Verspätungsgrund übermittelt“**. Dies bedeutet nicht, dass keine Ursache existiert, sondern lediglich, dass die öffentlich abrufbare Tafel keine konkrete nennt.
- Eine schon vor dem Update von Bahnmonitor gespeicherte ältere Fahrt hat noch keinen Grund. Die externe Stationstafel hat nur eine begrenzte historische Abdeckung; ältere Meldungen können nicht beliebig nachträglich abgerufen werden.

Die Beispielkarte `examples/lovelace_card.yaml` enthält den neuen Abschnitt **„Gemeldete Verspätungsgründe · RE 1 / RE 11“**, jeweils mit konkretem Streckenverlauf und Sollabfahrtszeit.

Datenstruktur belegt durch den veröffentlichten DBF-Quellcode: [DBInfoscreen/Stationboard.pm](https://github.com/derf/db-fakedisplay/blob/master/lib/DBInfoscreen/Controller/Stationboard.pm) und [JSON-API-Tests](https://github.com/derf/db-fakedisplay/blob/master/t/22-json.t). Die darin enthaltenen Verspätungsgründe stammen aus dem IRIS-Fahrgastinformationssystem und sind nicht immer vorhanden.

## Verständliche Fahrtrichtungen und nachvollziehbare Zugwende (v0.3.4)

Statt unklarer Bezeichnungen `Hinrichtung`/`Gegenrichtung` verwendet die Streckenlage echte Bahnhofsnamen, z. B. **Göttingen → Leinefelde** beziehungsweise **Leinefelde → Göttingen**. Die neuen Attribute `direction_labels`, `same_direction.label` und `reverse_direction.label` erleichtern die Dashboard-Darstellung. Die textuelle Begründung `reason` nennt nun ebenfalls die konkreten Bahnhöfe.

Für einen am **Startbahnhof** vermuteten Zugumlauf versucht die Integration nun zwei **unabhängige IRIS-Tafelmeldungen** zu korrelieren: den abfahrenden Zug am gegenüberliegenden Bahnhof und seine spätere Ankunft am Wendebahnhof. Die Meldungen müssen übereinstimmende Linie, Ziel, eine plausible Fahrtdauer (20–55 Minuten) und bei vorhandenen Verspätungswerten eine Abweichung von höchstens 10 Minuten haben. Bei mehreren passenden Fahrten bleibt die Zuordnung **uneindeutig**. Die Verknüpfung ist **kein Nachweis derselben Fahrzeuggarnitur**, da IRIS über diese Methode keine gesicherte Fahrzeugumlauf-ID liefert.

**Beispiel aus der Diagnose vom 08.10.2026:** Leinefelde Abfahrt **15:18 +18** (Prognose 15:36), Göttingen Ankunft **15:51 +17** (Prognose 16:08), Göttingen nächste Abfahrt **16:09**. Das ergibt **eine Minute errechnete Wendezeit**. Bei einem in der Konfiguration angenommenen Mindestpuffer von **10 Minuten** liegt die theoretisch früheste Folgeabfahrt bei **16:18 Uhr** bzw. **9 Minuten nach der Sollabfahrt**. Das ist **kein amtlich gemeldeter Verspätungswert**. Bahnmonitor hält die derzeit gemeldete eigene Abfahrtsprognose unverändert und zeigt den Wendehinweis **zusätzlich** an. Der Nutzer hatte die Folgeabfahrt auch als 16:08 bezeichnet; die vom System am 08.10. gespeicherte Sollabfahrt beträgt weiterhin 16:09. Diese Minute wird nicht stillschweigend geändert.

Neue Attribute unter `journeys[0].turnaround` sind `incoming_route`, `incoming_origin_station`, `incoming_departure_planned`, `incoming_departure_predicted`, `incoming_departure_delay_minutes`, `incoming_planned`, `incoming_predicted`, `outgoing_route`, `outgoing_planned`, `turnaround_buffer_minutes`, `minimum_turnaround_minutes`, `earliest_plausible_outgoing`, `estimated_minimum_followup_delay_minutes`, `incoming_departure_match` und `confirmed_vehicle`. Alle sind beim Fehlen einer eindeutigen Quelle entsprechend leer oder unbestätigt.

Die Beispiel-Dashboard-Karte `examples/lovelace_card.yaml` enthält einen eigenen Abschnitt **„Mögliche Vorleistung → Zugwende → Deine Fahrt“**.

## Schneller Home-Assistant-Start und transparenterer 7-Tage-Fahrplan (v0.3.3)

Die Diagnose vom 08.10.2026 zeigte **rund 38 Sekunden** Einrichtungszeit pro Eintrag. Die erste Aktualisierung wartete bislang auf den Download und das Einlesen des großen GTFS-Regionalbahn-Feeds sowie auf ggf. fehlgeschlagene Zusatz-APIs.

**Neues Startverhalten:** Die Bahnmonitor-Sensoren werden sofort mit dem deutlich gekennzeichneten Zustand **„Wird geladen“** registriert. Anschließend läuft der erste vollständige Datenabruf als **verwaltete Home-Assistant-Hintergrundaufgabe**. Sie wird bei Entladen des Eintrags automatisch beendet. Mehrere Einträge teilen sich weiterhin denselben GTFS-Download. Dadurch soll der Home-Assistant-Start wesentlich weniger Zeit beanspruchen; wie lange der Fahrplan im Hintergrund benötigt, zeigen die Diagnosewerte `last_download_seconds` und `last_parse_seconds`. Die Entitäten zeigen erst nach erfolgreichem Abruf reale Quelldaten. **Das ist kein schnellere Fahrplan-API**, sondern eine Entkopplung vom Startvorgang.

**7-Tage-Abdeckung:** Ein fehlender direkter RE-1-/RE-11-Fahrplaneintrag wird ausdrücklich **nicht** als Ausfall behandelt. Bei einem GTFS-Abgleich ohne Treffer werden je Fahrtag nun `gtfs_match_reason`, `gtfs_nearest`, `gtfs_origin_services` und eine verständliche `message` gespeichert. Damit ist z. B. sichtbar, ob die Linie am Startbahnhof im veröffentlichten Feed vorkommt, aber ihr dort erfasster Zug einen anderen Endbahnhof hat. Der Abgleich ergänzt die Rohdaten unter `diagnostics.gtfs_schedule.recent_matches` mit `origin_service_count` und `nearest_origin_departures` samt letztem eingetragenem Halt.

Die letzte Diagnose hatte am **13.10. keine durchgehende GTFS-Verbindung** und am **14.10. nur einen späteren passenden RE-1-Zug um 20:09 Uhr** gezeigt. Hieraus folgt weder ein verbindlicher Zugausfall noch ein Fehler der Integration. Die veröffentlichten GTFS-Basisfeeds enthalten keine verlässliche **Echtzeitbestätigung**. Die zusätzlich verwendete transport.rest-API antwortete weiterhin mit HTTP 503. Bis eine geeignete weitere, unabhängige Fahrplan-/Betriebsdatenquelle gefunden und getestet ist, markiert Bahnmonitor solche künftigen Fahrten weiterhin als **unbestätigt** statt eine Sicherheit vorzutäuschen.

## Präzise Abfahrtsprognosen und 7-Tage-Befund (v0.3.2)

Die Diagnose am 08.10.2026 um 08:44 zeigte RE 1 um 08:09 mit +45 Minuten und um 08:43 mit +45 Minuten. Beide Züge wurden irreführend als frühere Fahrten dargestellt, obwohl die **prognostizierten** Abfahrten erst um 08:54 bzw. 09:28 Uhr lagen. Die neue Streckenlage unterscheidet daher:

- **Soll- und Prognosezeit vergangen:** Beide Uhrzeiten sind überschritten; daraus folgt weiterhin keine bestätigte tatsächliche Abfahrt.
- **Trotz verstrichener Sollzeit noch erwartet:** Der Zug war planmäßig früher fällig, die aktuell zuletzt bekannte Abfahrtsprognose liegt aber noch in der Zukunft. Die Attribute `awaiting_count`, `same_direction.awaiting_departures` und `reverse_direction.awaiting_departures` zeigen diese Fälle.
- **Bevorstehende Sollabfahrt:** Der veröffentlichte Soll-Abfahrtszeitpunkt liegt noch in der Zukunft (`upcoming_scheduled_count`).

Alle drei Kategorien können zur Streckenlage beitragen, werden aber in `reason` und `trigger_reasons` unterschiedlich bezeichnet. Der Indikator bleibt **keine Aussage über die tatsächlich erfolgte Abfahrt**.

Die 7-Tage-Auswertung der Diagnose ergab: Donnerstag 08.10. 16:09 Uhr im GTFS-Feed gefunden, Dienstag 13.10. keine passende Direktverbindung, Mittwoch 14.10. nur eine weitere, abweichende Sollabfahrt um 20:09 Uhr. Wir melden diese Tage weiterhin als **unbestätigt**, nicht als Fahrtausfall. Der externe v6-Fahrplandienst meldet HTTP 503; ohne eine zweite verlässliche Quelle kann die Ursache der fehlenden GTFS-Fahrten nicht abschließend geklärt werden.

## Stabilitätskorrekturen in v0.3.1

Die Home-Assistant-Diagnose der 16:09-Fahrt vom 08.10.2026 zeigt, dass der Regionalverkehrs-GTFS-Feed geladen ist und den RE 1 Göttingen → Leinefelde für 16:09–16:42 Uhr enthält. Für Dienstag (13.10.) und Mittwoch (14.10.) fehlt dagegen noch ein zugeordneter GTFS-Treffer; der v6-Dienst antwortet mit HTTP 503.

- **Streckenlage:** Der Fehler, durch den der Status `Stark gestört` fälschlich als `Abfahrtsprognose` erschien, ist korrigiert. Die Begründung erwähnt weiterhin ausdrücklich, ob eine frühere Fahrt oder eine bevorstehende Prognose gemeint ist.
- **GTFS-Diagnostik je Fahrtag:** Unter `diagnostics.gtfs_schedule.recent_matches` erscheinen die Gründe für fehlende Treffer: `line_not_in_feed`, `no_active_calendar_service`, `no_matching_direct_route`, `no_departure_within_search_window`, `ambiguous_multiple_departures` oder `matched`. Angezeigt werden außerdem die nächstgelegenen Sollabfahrten und die Anzahl passender Kandidaten. Ein fehlender Treffer ist **kein bestätigter Ausfall**.
- **Wendebahnhof:** Bei einem bestehenden Eintrag kann noch `turnaround_at=legacy` stehen. Die Diagnose ergänzt `effective_turnaround_at`, damit die tatsächlich verwendete Einstellung sichtbar wird. Unter **Konfigurieren** kann Start, Ziel oder keiner ausdrücklich gewählt werden.
- **Sicherere Binärsensoren:** Bei reinen GTFS-Sollfahrten bleiben **Zugausfall** und **Mögliche Folgeverspätung** als nicht verfügbar markiert, statt ein scheinbares „alles gut“ zu melden. Ein nicht bestätigter Linienabgleich darf ebenfalls keine bestätigte Zugausfallinformation erzeugen.

Bitte nach Update auf v0.3.1 eine neue **Diagnose herunterladen**. Die neuen `recent_matches`-Werte für 13.10. und 14.10. helfen, den Grund für die fehlenden Fahrten gezielt zu beheben. Die GTFS-Zusatzdiagnose ist keine operative Zugmeldung.

## Installation

1. Repository in HACS als **benutzerdefinierte Integration** hinzufügen: `https://github.com/adrianwieg/ha-bahnmonitor`.
2. Bahnmonitor in HACS herunterladen und Home Assistant neu starten.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Bahnmonitor**.
4. Jede deiner regelmäßig benötigten Fahrten als eigenen Eintrag konfigurieren (z. B. RE 1 um 16:09 Uhr an Dienstagen, Mittwochen und Donnerstagen).

Bei privatem Repository benötigt HACS Zugriff bzw. einen erneuten temporären Public-Status, um Updates zu laden.

## Einrichtung über die GUI

- **Name**, **Startbahnhof**, **Zielbahnhof**, **Linie** (z. B. RE 1), **Sollabfahrtszeit** (HH:MM)
- **Wochentage:** `0,1,2,3,4` für Mo–Fr, `1,2,3` für Di–Do
- **Suchfenster**: Abweichung zur erwarteten Sollabfahrt
- **Streckenlage**: ganztägige Beobachtung beider Richtungen
- **Wendebahnhof**: `Keiner`, `Startbahnhof` oder `Zielbahnhof`
- **Wendepuffer**: frei konfigurierbare angenommene Mindestzeit und maximaler Abstand zur vorausgehenden Ankunft

Die Auswahl des Wendebahnhofs ist **nicht auf Göttingen beschränkt**. Für eine mögliche Folgeverspätung **vor deiner Abfahrt** ist allerdings nur ein Wendebahnhof am **Startbahnhof** deiner Fahrt relevant. Wählst du den Zielbahnhof, ist das eine mögliche spätere Wende **nach** deiner Fahrt und wird deshalb nicht als Vorleistung für deine Abfahrt ausgegeben. Ein als `Keiner` ausgewählter Wendebahnhof schaltet die Prüfung aus.

Für bestehende Einträge bleibt die bisherige Einstellung `turnaround` kompatibel. Bitte bei Bedarf die neue Auswahl unter **Konfigurieren** ausdrücklich setzen.

### Vorsicht bei der Fahrzeugdurchbindung

IRIS kann ankommende Züge derselben Linie am Wendebahnhof anzeigen. Daraus folgt **nicht**, dass es derselbe Triebwagen ist, der als dein Zug abfährt. Bahnmonitor kennzeichnet die Verbindung ausdrücklich als **unbestätigt**. Bei mehreren möglichen ankommenden Zügen wird `ambiguous` ausgegeben, nicht eine zufällige Wahl. Eine angezeigte Folgeverspätung ist daher ein Hinweis, keine betriebliche Bestätigung. Ankünfte und Abfahrten werden aus einer gemeinsamen IRIS-Stationstafel abgerufen, um die öffentliche Rate-Limit-Vorgabe einzuhalten.

## Datenquellen und 7-Tage-Horizont

Die Datenquellen werden bewusst **getrennt** genutzt:

| Quelle | Verwendung | Verlässliche Aussage |
|---|---|---|
| **GTFS Deutschland – Regionalverkehr** | Sollfahrten im kommenden 7-Tage-Zeitraum, täglicher Feed | Die konfigurierte Verbindung ist im veröffentlichten Sollfahrplan enthalten |
| **DBF/IRIS-TTS** | Kurzfristige Abfahrten, Verspätungen, Ausfälle, Gleise und Streckenlage | Gemeldeter aktueller Zugstatus, soweit verfügbar |
| **db.transport.rest** | Zusätzlicher Best-Effort-Fallback, wenn GTFS/IRIS eine Fahrt nicht finden | Angaben nur soweit der Drittanbieter verfügbar ist |

**GTFS:** Der regionale Sollfahrplan wird von `https://download.gtfs.de/germany/rv_free/latest.zip` geladen, **einmal pro Tag und gemeinsam für alle konfigurierten Fahrten**. Die ZIP-Datei umfasst üblicherweise etwa 12 MB. Das Parsing erfolgt außerhalb des Home-Assistant-Eventloops. Ein einmaliges Aktualisierungsproblem wird mit Wartezeit erneut versucht. Bei mehr als 24 Stunden alten Feed-Daten werden Ergebnisse als **veraltet** markiert. Ein gefundener Plan-Zug ist **nicht pünktlich bestätigt**, und eine im Feed nicht gefundene Fahrt ist **kein bestätigter Ausfall**.

**IRIS:** Die öffentliche DBF-Version-3-Stationstafel bietet nur eine begrenzte Zeitspanne. Die Abfragen werden pro Station zusammengeführt, zwischengespeichert und rate-limitiert. Bitte beachten: Dies ist ein privat betriebener Dienst ohne Verfügbarkeitszusage.

**db.transport.rest:** Diese Quelle kann HTTP 503 liefern. Es erfolgt ein gestaffelter Retry-Backoff, ohne einzelne Fahrten irrtümlich als ausgefallen zu kennzeichnen.

Für einen **bestätigten längerfristigen Ausfall** (z. B. mehrere Tage vor der Fahrt) reichen statische GTFS-Sollfahrpläne nicht aus. Dafür wäre eine zusätzliche verlässliche Echtzeit-/Störungsquelle wie autorisierte DB-RIS-Daten notwendig. Eine solche Quelle ist derzeit **nicht angebunden**.

### Datenherkunft und Lizenz

GTFS Deutschland / NeTEx-Datenbasis **DELFI e.V.** – **Creative Commons Attribution 4.0 (CC BY 4.0)**. Quelle: [GTFS Deutschland – Schienenregionalverkehr](https://gtfs.de/de/feeds/de_rv/). Die Informationen werden ohne Gewähr übernommen. Projektcode und GTFS-Fahrplandaten sind voneinander getrennt; der Feed wird nicht ins Repository eingecheckt.

## Streckenlage – ganztägig

Alle 10 Minuten werden aktuelle RE-1-/RE-11-Meldungen **beider Richtungen** abgerufen, unabhängig von der eigenen Abfahrtszeit. Bereits erfasste Fahrten des aktuellen Tages werden in Home Assistant gespeichert und nach Neustart wieder geladen. Die letzten drei erfassten Züge pro Richtung werden berücksichtigt, außerdem aktuelle Abfahrtsprognosen.

Ein separater Text **`reason`** erklärt die Einstufung mit Uhrzeiten, Linie, Richtung und Verspätung. Unter `trigger_reasons` stehen die einzelnen auslösenden Meldungen. Frühere Züge und erst bevorstehende Prognosen werden eindeutig unterschieden.

- **Unauffällig:** bei ausreichenden Beobachtungen keine meldungspflichtige Abweichung erkannt
- **Wenig Daten:** bisher zu wenige Hinweise für eine Bewertung
- **Auffällig:** mindestens ein Zug mit drei oder mehr Minuten gemeldeter Verspätung
- **Stark gestört:** Ausfall oder mindestens zwei gemeldete Verspätungen ab zehn Minuten
- **Daten veraltet / Datenquelle gestört:** keine aktuelle Stationstafel verfügbar

**Wichtig:** Die Streckenlage sagt nur etwas über die **erfassten Züge** aus. Eine erhöhte Verspätung am Morgen garantiert keinerlei Verspätung des eigenen Zugs am Nachmittag. Historische Züge, die während eines längeren Ausfalls nicht erfasst wurden, können fehlen.

## Home-Assistant-Entitäten

Pro Fahrt gibt es mindestens folgende Entitäten:

- **Nächste Fahrt** – lesbarer deutscher Status, Attribute `journeys`, `display_summary`, `display_departure`, `display_delay`, `display_platform`, `realtime_checked` und `timetable_confirmed`
- **Streckenlage** – Status mit `summary`, `reason`, `trigger_reasons`, `same_direction`, `reverse_direction`
- **Fahrplandienst** – Überblick über Datenquellen, Fehler und Retry
- **Zugausfall** – nur bei tatsächlich gemeldetem Zugausfall gültig
- **Mögliche Folgeverspätung** – nur bei eindeutiger, aber **nicht fahrzeugseitig bestätigter**, plausibler ankommender Vorleistung

Die Entity-IDs kann Home Assistant nach deinen gewählten Gerätenamen selbst vergeben.

## Dashboard

[**Lovelace-Beispielkarte**](examples/lovelace_card.yaml): Im Dashboard **Bearbeiten → Karte hinzufügen → Manuell** und den YAML-Inhalt einfügen. Entity-IDs bei Bedarf anpassen. Die Karte funktioniert mit Standard-Home-Assistant-Karten ohne zusätzliche Frontend-Plugins und zeigt die konkrete Begründung der Streckenlage an.

## Fehleranalyse

In **Einstellungen → Geräte & Dienste → Bahnmonitor → ⋮ → Diagnose herunterladen** erhältst du die Quellstatus-Informationen einschließlich GTFS- und IRIS-Diagnose sowie der einzelnen Fahrten.

Für zusätzliche Protokolle in einer vorhandenen `logger:`-Konfiguration ergänzen:

```yaml
logger:
  logs:
    custom_components.bahnmonitor: debug
```

Zugprognose und Streckenlage sind unabhängig. Die Streckenlage beginnt ganztägig, die zugbezogene IRIS-Prüfung kann näher an der Abfahrt beginnen. Bitte Diagnosedaten vor öffentlicher Weitergabe auf persönliche Fahrtzeiten prüfen.

## Entwicklungsstand

Diese Version enthält neue GTFS- und Wendebahnhof-Funktionen, die noch unter einem echten Home Assistant auf Kompatibilität und Datenqualität verifiziert werden müssen. Ziel ist die zuverlässige Trennung von **Sollfahrplan**, **aktuellem Zugstatus**, **Streckenindikator** und **unbestätigtem Wenderisiko**. Push-Benachrichtigungen sind bewusst nicht Bestandteil dieses Ausbauschritts.
