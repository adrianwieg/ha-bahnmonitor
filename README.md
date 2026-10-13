# Bahnmonitor – Home Assistant

Generische Home-Assistant-Integration für **Streckenmeldungen** und optional **wiederkehrende Zugfahrten** in Deutschland. Start und Ziel sind frei konfigurierbar. Göttingen–Leinefelde ist nur ein Testbeispiel, keine feste Vorgabe.

**Entwicklungsstand:** Die neue Streckenüberwachung benötigt einen Praxistest mit Home Assistant und tatsächlich veröffentlichten SEV-Meldungen. Die Datenquellen sind keine amtlich vollständige Baustellen- oder Sperrungsdatenbank.

## Fehlerrobuste Bahnhofssuche (v0.4.1)

Die Bahnhofssuche nutzt **zunächst das statische DB-Stationsverzeichnis**
(`/stations`), bei fehlendem eindeutigem Treffer zusätzlich die
**dynamische Bahnhofssuche** (`/locations`). Das Stationsverzeichnis
funktioniert unabhängig von der Live-Bahnhofssuchlogik und wird für
beliebige Bahnhöfe verwendet – nicht nur für die Teststrecke.

Falls beide Dienste vorübergehend ausfallen, **kann die Strecke trotzdem
eingerichtet werden**. Der Sensor `Streckenlage` zeigt dann
`Bahnhofsdaten fehlen` mit `station_lookup_pending`. Die Integration
versucht die fehlenden EVA-IDs im Hintergrund in Abständen von 15 Minuten
erneut zu laden und speichert sie nach erfolgreicher Auflösung. Währenddessen
werden weder eine fiktive Störung noch ein störungsfreier Verkehr behauptet.

Wird bei **erreichbaren** Suchdiensten kein eindeutiger Bahnhof gefunden,
ist der vollständige Bahnhofsname (z. B. `Erfurt Hbf`) erforderlich.
Alternativ kann eine bekannte, gültige **numerische EVA-ID** direkt in das
Bahnhofsfeld eingetragen werden. Sie benötigt bei der Einrichtung keine
Online-Bahnhofssuche.

## v0.4.8: Unabhängige Verbindungen mit Transitous (MOTIS)

**Neu:** Eine zusätzliche Verbindungsauskunft über die eigenständige,
öffentliche [Transitous/MOTIS-API](https://transitous.org/api/)
(`https://api.transitous.org/api/v6/plan`).
Sie ist **nicht** von `v6.db.transport.rest` abhängig. Diese
Anbindung ist zunächst **standardmäßig deaktiviert**.

### Freigabe des Transitous-Betreibers

Die [Transitous-Nutzungsrichtlinie](https://transitous.org/api/) setzt
ein nichtkommerzielles Open-Source-Projekt, einen identifizierbaren
User-Agent und die Attribution der Datenquellen voraus. Für
potenziell kostenintensive Routingabfragen bittet der Betreiber
ausdrücklich um **Kontaktaufnahme vor dem Einsatz**.

Der Quellcode dieses Projektes steht jetzt unter der **MIT-Lizenz**
(siehe `LICENSE`). Vor dem Einschalten der Transitous-Routingabfragen
bitte unter [Transitous API Policy](https://transitous.org/api/) über den
angegebenen Matrix-Kontakt mit den Betreibern die zu erwartende
Last abstimmen. Bahnmonitor nutzt maximal vier `plan`-Abfragen
(je zwei Richtungen, bei Abend-Abfragen zusätzlich den frühen Morgen)
sowie die einmalige Haltestellensuche und wartet nach einer Abfrage
mindestens **zwei Stunden**. Beim HTTP-429-/503-Fehler wird kein
sofortiger Wiederholungssturm ausgelöst.

**Erst nach bestätigter Abstimmung** in den Bahnmonitor-Optionen
`Transitous aktivieren (nur nach Betreiberfreigabe)` einschalten.
Vorher werden **keine Requests** an Transitous gesendet. Diese
Einstellung bestätigt, dass die Betreiber über diese Nutzung
informiert wurden und damit einverstanden sind.

### Funktionsweise

Die Integration sucht automatisch die zu `Startbahnhof → Zielbahnhof`
gehörenden Transitous-Haltestellen (`/api/v1/geocode`) und fragt
`/api/v6/plan` unabhängig von den üblichen RE-Zugzeiten ab.
Mit einem 350-m-Radius werden auch nahe ZOB-/Busbahnhof-Halte
innerhalb eines Bahnhofskomplexes berücksichtigt; es sind
keine extra SEV-Haltestellen im Bahnmonitor einzutragen.

Transitous liefert einen Reiseplan mit einzelnen Bus- und
Zugabschnitten. Ein **direkter Bus** (mit höchstens kurzen
Fußwegen an Anfang/Ende) geht in den Sensor
**Direkte Busverbindungen** ein. Eine Verbindung mit
Umstieg erscheint getrennt im Sensor **Alternative Verbindungen**.
Die korrekte Abfahrtszeit kommt aus dem Fahrplan
(`scheduledStartTime`), niemals aus einer vermeintlichen
RE-Abfahrt. Für spätabendliche Suchen wird ein zusätzliches
Zeitfenster am nächsten Morgen berücksichtigt.

Die bestehende Datenquelle bleibt in der Diagnose unterscheidbar:
`route_health.bus_connections.primary_status`,
`route_health.bus_connections.fallback_status`,
`route_health.transitous` und `source_debug.transitous`.
**Keine Antwort / API-Ausfall** bedeutet nicht, dass kein Bus fährt.
Eine Fahrt namens „Bus RE1“ ist Fahrplaninformation; nur ein
ausdrücklicher SEV-Hinweis bestätigt die
Schienenersatzverkehrsmeldung.

**Quellenhinweis:** [Transitous-Datenquellen](https://transitous.org/sources/)
sowie die jeweilige GTFS- und OpenStreetMap-Attribution beachten.

**Aktuelle Grenze:** Die konkreten RE1-Fahrten Leinefelde
21:25 Uhr und 04:57 Uhr sind im DB Navigator nachgewiesen,
aber ihre Verfügbarkeit im Transitous-Datensatz ist noch
**nicht live überprüft**. Die neuen Tests nutzen
reproduzierbare Testantworten, keinen echten Produktivaufruf.

## v0.4.7: Automatische Zusammenfassung eines Bahnhofskomplexes

Für die Konfiguration genügt jetzt auch bei Schienenersatzverkehr eine
normale Strecke, beispielsweise **Leinefelde → Göttingen**. Die Integration
versucht selbstständig, den passend benannten **Bahnhof/ZOB- oder
Busbahnhof-Halt** jeder Station zu finden, ohne eine zusätzliche
Bus-Haltestellen-Konfiguration vorauszusetzen. Rail-EVA-IDs und
Bus-Haltestellen-IDs werden bei der **Datenabfrage** getrennt gehalten,
in der Streckenüberwachung aber derselben Station zugeordnet.

Die optionale Linienauswahl gilt weiterhin für die Züge. Busfahrten
werden unabhängig von den alten RE-Abfahrtszeiten gesucht. Die
Busverbindung wird im Sensor **Direkte Busverbindungen** angezeigt.

**Ausnahme:** Wenn ein öffentlicher Suchdienst mehrere ähnlich passende
Bus-Halte am Bahnhof liefert, wählt Bahnmonitor absichtlich nicht
willkürlich einen aus. In diesem Fall können in den Optionen weiterhin
zwei **manuelle Haltestellen-Overrides** angegeben werden. Die Namen
sind sonst nicht erforderlich.

Diese Änderung verwendet für die Verbindungssuche das dokumentierte
API-Profil **dbnav** (statt dbweb). Der Benutzer gibt weiterhin
**keine** zweite Strecke an. Die Überwachung bleibt generisch und
funktioniert nicht nur für Leinefelde/Göttingen.

Der Ausfall einer öffentlichen Verbindungs-API mit HTTP 503 ist dadurch
jedoch **nicht** behoben: Können die Bus-Haltestellen nicht aufgelöst
oder die Verbindungen nicht abgerufen werden, ist deren Zustand
unbekannt, nicht fälschlich störungsfrei.

## v0.4.6: Getrennte Haltestellen für Bus-Ersatzfahrten

Die DB-Fahrplanauskunft kann einen **Bus RE1** als direkte Verbindung
zwischen eigenständigen Bus-Haltestellen anbieten, die in den
IRIS-Bahnhofstafeln der beiden Bahnhöfe fehlen.

Als belegtes Beispiel aus dem DB Navigator am 13.10.2026:
- 21:25 Uhr **Leinefelde Bahnhof, Leinefelde-Worbis**
- 21:45 Uhr **Bahnhof/ZOB, Heilbad Heiligenstadt**
- 22:30 Uhr **Bahnhof/ZOB, Göttingen** (durchgehender Bus RE1)

**Einrichtung:** In den Optionen einer Streckenüberwachung gibt es jetzt
zwei neue, optionale Felder. Trage jeweils den exakten Namen aus der
Fahrplanauskunft ein:

- Bus-Abfahrtshaltestelle: `Leinefelde Bahnhof, Leinefelde-Worbis`
- Bus-Zielhaltestelle: `Bahnhof/ZOB, Göttingen`

Im generischen Projekt sind beide Felder standardmäßig leer. Jede Strecke
kann eigene Haltestellen haben. Eine reine Streckenüberwachung funktioniert
weiterhin ohne diese optionalen Felder.

**Ergebnis:** Die Integration löst für diese Namen eigenständige
Bus-Haltestellen-IDs auf und fragt die Verbindungen im getrennten
`dbweb`-Profil ab. Sie verwendet dabei nicht die Bahn-EVA-IDs. Es werden
direkte Busfahrten einschließlich geplanter Abfahrt/Ankunft, der
Linienbezeichnung und expliziter Störungsmeldungen gesammelt. Bei
Abfragen ab 16:00 Uhr wird zur Abdeckung früher Fahrten des nächsten
Tages zusätzlich das Zeitfenster ab 04:00 Uhr gesucht.
Dafür ist **kein** Abgleich mit regulären RE-Abfahrtszeiten nötig.

Neuer Sensor: **Direkte Busverbindungen** mit Attributen
`services`, `source_status`, `errors` und `bus_stops`.
Die Ergebnisse erscheinen auch im Diagnoseexport unter
`source_debug.bus_connections`.

**Wichtige Einschränkung:** Die zusätzliche Abfrage verwendet noch
denselben öffentlichen Domainbetreiber `db.transport.rest` mit einem
anderen API-Profil. Wenn der Dienst in beiden Profilen HTTP 503 meldet,
bleiben auch die Busfahrten zunächst unbekannt. `Bus RE1` belegt
eine Busfahrt, aber für eine automatische **SEV-Bestätigung** ist eine
explizite Ersatzverkehrs-Meldung erforderlich. Die Daten werden
maximal einmal pro Stunde erneut abgefragt; alle Fehler stehen in
der Diagnose. Diese Version ersetzt keine unabhängige Datenquelle.

## v0.4.5: Zeitliche Plausibilität von Bus-Teilstrecken

Ein gemeinsamer Zwischenbahnhof alleine ergibt **keine** Reiseverbindung.
In einem Diagnoseexport vom 13.10.2026 wurde Bus RE8 um 21:20 Uhr ab
Leinefelde nach Eichenberg beobachtet. Die im Beispiel sichtbaren Züge
RB83/RB87 hatten ihre Ankunft in Göttingen schon um 18:45 bzw.
19:45 Uhr – vor der Busabfahrt. Das kann kein Anschluss sein.

Darum zeigt `sensor.*_bus_teilstrecken` jetzt **zweierlei**:
- `observed_bus_sections`: tatsächlich beobachtete Busabschnitte,
  einschließlich Zeit, Linienname, Teilstrecke und möglichem Umsteigepunkt.
- `candidates`: nur Teilstreckenpaare, deren **Endpunkt-Zeitfolge**
  nicht bereits widersprüchlich ist. Auch das beweist keinen nutzbaren
  Anschluss; dafür fehlen im IRIS-Endpunkttafel-Datensatz die konkreten
  Ankunfts-/Abfahrtszeiten am Umsteigepunkt.

Ein Abschnitt bleibt sichtbar, auch wenn alle beobachteten Züge
zeitlich davor fahren. Es wird **keine** durchgehende A–B-Fahrt und
kein offizieller SEV erfunden. Die vollständige Auskunft für beliebige
Umsteigewege benötigt eine funktionierende Verbindungsschnittstelle
oder eine geeignete weitere Fahrplandatenquelle.

## v0.4.4: Teilstrecken über gemeinsame Umsteigebahnhöfe

Der öffentliche Fahrplandienst für vollständige Verbindungen kann HTTP 503
melden, während die IRIS-Bahnhofstafeln weiterhin Busfahrten und Züge
liefern. Eine einzelne Busfahrt muss die komplette überwachte Strecke
**nicht** bedienen.

Neu ist deshalb die **geografisch/strukturelle Verknüpfung von
Teilstrecken**: Die Integration vergleicht die Abfahrtstafeln am ersten
Endpunkt mit den Ankunftstafeln am zweiten (auch in Gegenrichtung). Endet
eine als Bus gekennzeichnete Fahrt an einem Bahnhof, den eine Zugfahrt
auf der anderen Seite laut veröffentlichtem Halteverlauf bedient,
wird die Busfahrt als **mögliche Bus-Teilstrecke** angezeigt.

Das bleibt eine **unbestätigte Zuordnung**: Gleicher Umsteigebahnhof
belegt weder ausreichende Umsteigezeit noch die offizielle
Ersatzverkehrs-Funktion eines Busses. Eine andere Bus-Liniennummer
wird deshalb **nicht automatisch** der optional eingestellten
Bahnlinie zugeschrieben.

Ein tatsächliches Beispiel aus einem Diagnoseexport zeigt:
Bus RE8 fährt um **21:20** ab Leinefelde bis **Eichenberg**. In den
Göttinger Tafeln stehen RB83/RB87 über Eichenberg. Obwohl keine
durchgehende Fahrt Leinefelde–Göttingen erscheint, kann dieser Bus
als möglicher Teilabschnitt angezeigt werden. Es wird **keine**
durchgehende Reiseverbindung und **kein bestätigter SEV** daraus abgeleitet.

Der neue Sensor **Bus-Teilstrecken** enthält die beobachteten
Abschnitte mit Buslinie, Uhrzeit, Halteverlauf, gemeinsamem
Umsteigebahnhof und dem Flag `line_filter_match`.
Außerdem sind sie im `corridor`-Attribut des Sensors `Streckenlage`
und unter `source_debug.corridor` im Diagnoseexport zu finden.

Fehlen ausdrücklich veröffentlichte SEV-Meldungen, kann die
Streckenlage jetzt **Bus-Teilstrecke gefunden (SEV ungeprüft)**
melden, selbst wenn die Verbindungssuche HTTP 503 liefert.
Der Binärsensor **Schienenersatzverkehr** bleibt dann unverfügbar,
statt SEV als bestätigt zu markieren.

Die allgemeine Lovelace-Vorlage
[`examples/route_monitor_card.yaml`](examples/route_monitor_card.yaml)
zeigt Bus-Teilstrecken separat von offiziellen Störungsmeldungen.

## v0.4.3: SEV-Debugging bei abweichenden Busfahrzeiten

Für die Frage „Warum erkennt Bahnmonitor den SEV nicht?“ gibt es jetzt zwei
Diagnosewege:

**1. Automatisch bei jeder Streckenabfrage:** Home Assistant erzeugt einen
Sensor **Datenquellen-Debug**. Unter dessen Attributen sind die Mengen an
IRIS-Rohfahrten, Streckentreffern, Linien-Treffern und Bus-/SEV-Markierungen
pro Richtung sichtbar. Die vollständigen Einzelbeispiele stehen im
herunterladbaren **Integrations-Diagnoseexport** unter
`source_debug.station_boards`. Pro Richtung und Ankunft/Abfahrt werden bis
zu 30 priorisierte Fahrten ausgewiesen, auch wenn sie aufgrund falscher
Fahrtrichtung, fehlender Linienbezeichnung oder einer anderen Abfahrtszeit
nicht in der üblichen Streckenliste erscheinen.

Das Feld `excluded_by` erklärt genau, ob der Fahrt die ausgewählte
Endstation fehlt (`ziel_oder_streckenabschnitt_passt_nicht`), die Linie
nicht passt (`linienfilter_passt_nicht`), keine zuordenbare Bahnlinie
vorliegt (`keine_eindeutige_bahnlinie`) oder die Fahrt außerhalb des
aktuellen Beobachtungsfensters liegt (`ausserhalb_zeitfenster`).
`explicit_sev: true` bedeutet eine ausdrücklich veröffentlichte
Ersatzverkehr-Kennzeichnung; `is_bus_label: true` bedeutet nur, dass die
Bezeichnung nach einem Bus aussieht, **nicht** zwingend SEV.

**2. Manuelle zeitversetzte Verbindungsabfrage:** In
**Entwicklerwerkzeuge → Aktionen** folgende Aktion ausführen:

```yaml
action: bahnmonitor.debug_verbindung
data:
  time: "12:00"
```

Die Aktion fragt **einmal für beide Richtungen** jeweils bis zu sechs
Verbindungen ab dem nächsten Zeitpunkt 12:00 Uhr (heute oder morgen) ab.
Die tatsächlichen Ersatzbuszeiten stehen anschließend im Diagnoseexport unter
`source_debug.manual_probes`. Bei mehreren Bahnmonitor-Einträgen
`entry_id` in der Aktion ergänzen. Für weitere Tageszeiten die Aktion
mit z. B. `07:00` oder `17:00` erneut aufrufen, aber bitte nicht in
kurzen Abständen wiederholen: Der öffentliche Dienst hat Anfragelimits.
Ein HTTP-503-Fehler erscheint als solche Fehlermeldung im Diagnoseexport.
Manuelles Debugging ändert **keine** Live-Sensoren und ersetzt keine
vollständige Fahrplanauskunft.

**Diagnose exportieren:** Einstellungen → Geräte & Dienste →
Bahnmonitor → ⋮ → Diagnose herunterladen. Im JSON interessieren
`source_debug.station_boards`, `source_debug.journey_queries` und
`source_debug.manual_probes`. Besonders wichtig sind
`scheduled`/`planned_departure`, `train`, `line`,
`route`, `destination`, `excluded_by` und `error`.

**Wichtig:** Die automatische Verbindungssuche verwendet zurzeit
`/journeys` mit `results=6` und dem aktuellen Zeitpunkt. Das ist
**keine** vollständige Tagesabfrage. SEV auf Teilstrecken oder zu anderen
Zeiten kann damit fehlen, auch wenn er RE1/RE11 heißt. Diese Version
macht das Problem nachvollziehbar, bestätigt aber keinen Ersatzbus, dessen
Meldung von keiner Quelle geliefert wird.

## v0.4.2: Verkehrsmeldungen bei Ausfall des Verbindungsdienstes

Der Streckenmonitor behandelt den öffentlichen Verbindungsdienst und die
IRIS-Stationstafeln als **zwei getrennte Quellen**. Falls die Verbindungsabfrage
mit HTTP 503/429 fehlschlägt, können trotzdem solche Störungen angezeigt
werden, die als **explizite Meldung** für passende Fahrten in den erreichbaren
IRIS-Stationstafeln stehen. Insbesondere als `Bus SEV` gekennzeichnete Fahrten
oder ausdrücklich veröffentlichte Schienenersatzverkehr-Hinweise werden dabei
als SEV erkannt; gewöhnliche Busverbindungen hingegen **nicht**.

In `Streckenlage` stehen die zugeordneten Hinweise in `incidents` mit
`source=DBF/IRIS-TTS` bzw. `db.transport.rest`. Die tatsächliche
Quellenabdeckung bleibt über `alert_sources`, `alert_source_errors`,
`board_counts` und `alert_journey_count` sichtbar. Wenn der
Verbindungsdienst ausfällt und keine passende IRIS-Meldung vorliegt, lautet
die Lage **Daten unvollständig**, nicht „Keine Störungen“.

Die neue Meldungsauswertung erfindet **keinen** Ersatzverkehr. Wenn kein
Zug und keine SEV-Meldung in den Stationstafeln erscheinen und die
Verbindungssuche ausfällt, ist ein SEV weiterhin **nicht nachweisbar**.
Bei 0 zur Linie/Strecke passenden Ankünften und Abfahrten zeigt auch die
Wendeprüfung jetzt `insufficient_data` statt einer vermeintlichen
Bestätigung, dass es keine Wende gibt.

Der Diagnoseexport verwendet ab dieser Version dieselbe Versionsangabe wie
das Integrationsmanifest. In v0.4.1 stand intern noch irrtümlich `0.3.5`,
obwohl v0.4.1 installiert war.

## Streckenüberwachung – jetzt der Standard

Neue Monitor-Einträge benötigen nur **Name, Startbahnhof und Zielbahnhof**. Eine **Linie ist optional** (z. B. `RE 1` oder `RE 1, RE 11`). Keine feste Abfahrt, Wochentage oder manuelle Wendebahnhof-Wahl für den Streckenmodus erforderlich. Frühere Einzelzug-Konfigurationen bleiben aus Kompatibilitätsgründen editierbar.

### Was wird erfasst?

- **Aktuelle Abfahrten und Ankünfte in beide Richtungen**, auch solche ohne Verspätung: geplante und prognostizierte Zeiten, Abweichung, Zugausfall, Linie, Gleis und ausdrücklich veröffentlichte Verspätungsursachen. Der Echtzeit-Zeithorizont ist durch die veröffentlichten IRIS-Stationstafeln begrenzt.
- **Verkehrsmeldungen und SEV** aus aktuell abgefragten öffentlichen Verbindungen über `v6.db.transport.rest/journeys` (`routingMode=HYBRID`, Busabschnitte eingeschlossen): Baustellen, Sperrungen, Umleitungen, Ausfälle und ausdrücklich als Ersatzverkehr gekennzeichnete Busse. Ein regulärer Bus ist kein Nachweis für SEV.
- **Automatische Zugwenden**: Ankunfts- und Abfahrtstafeln beider Streckenenden werden nach Endbahnhof, übereinstimmender Linie und zeitlich passender Rückfahrt durchsucht. Für plausible Fälle berechnet die Integration, ob eine verspätete Ankunft rechnerisch eine Folgeverspätung begünstigen könnte. **Es handelt sich nie um eine bestätigte Fahrzeugumlaufzuordnung.** Bei mehreren möglichen Zügen lautet der Status `ambiguous`.

Beispiel Leinefelde → Göttingen: RE 1 soll **15:51** in Göttingen enden, wird **16:08** erwartet; die Rückfahrt Göttingen → Leinefelde soll **16:09** abfahren. Bei angenommenem Mindest-Wendepuffer von 10 Minuten bleibt rechnerisch eine Minute für die Wende und eine **mögliche** Folgeverspätung von neun Minuten – ausschließlich *falls dieselbe Garnitur* zurückfährt. Die gemeldete Abfahrtsprognose bleibt unverändert.

### Home-Assistant-Entitäten

Die Integration erzeugt für eine Strecke:
- Sensoren **Streckenlage**, **Nächste Abfahrt**, **Nächste Rückfahrt**, **Nächste Ankunft**, **Nächste Rückankunft**, **Zugwenden**, **Verkehrsmeldungen** und **Fahrplandienst**.
- Binärsensoren **Schienenersatzverkehr**, **Störungsmeldung**, **Zugausfälle** und **Mögliche Folgeverspätungen**. Wenn erforderliche Fahrplandaten fehlen, bleibt die Bewertung unbekannt/unverfügbar anstatt fälschlich grün.

Wichtige Attribute des Sensors `Streckenlage`: `timetable.departures`, `timetable.arrivals`, `turnaround.candidates`, `incidents`, `alert_source_status`, `sev_reported`, `direction_labels` sowie `same_direction` und `reverse_direction`.

Neue Echtzeit-Informationen werden alle zehn Minuten angefordert; Verbindungsmeldungen werden bis zu 30 Minuten zwischengespeichert. Mehrere Einträge teilen sich den Cache der Stationstafeln, um öffentliche Dienste zu schonen.

**Grenzen:** Die Datensätze enthalten keine vollständige tagelange Rückschau, keine sichere physische Zugumlauf-ID und kein vollständiges Baustellenregister. Bei fehlenden Meldungen ist die Strecke **nicht** automatisch störungsfrei. Die konkrete SEV-Erkennung muss anhand realer veröffentlichter Meldungen getestet werden. Ein optionaler Linienfilter begrenzt zunächst die IRIS-Zugbeobachtungen; veröffentlichte Verbindungsstörungen können die ganze Relation betreffen, ohne ihre einzelne Linie zu belegen.

Beispiel für eine vollständige Lovelace-Karte: [`examples/route_monitor_card.yaml`](examples/route_monitor_card.yaml).

**Öffentliche Schnittstelle:** [db.transport.rest](https://v6.db.transport.rest/api.html).

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
