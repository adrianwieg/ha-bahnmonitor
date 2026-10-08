# Bahnmonitor (Home Assistant, Prototyp v0.1.0)

Verwaltet wiederkehrende Zugfahrten über die Home-Assistant-GUI. Für drei tägliche Fahrten einfach **drei Integrationseinträge** hinzufügen; RE 1 und RE 11 zwischen Leinefelde und Göttingen können gemischt werden.

## Installation

1. Den Ordner `custom_components/bahnmonitor` nach `/config/custom_components/bahnmonitor` kopieren.
2. Home Assistant neu starten.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Bahnmonitor**.
4. Für jede der drei täglichen Fahrten einen Eintrag erstellen. Abfahrtszeit HH:MM; Wochentage: Montag=0, Dienstag=1, ... Sonntag=6. `0,1,2,3,4` bedeutet Mo–Fr.
5. Unter **Konfigurieren** kann jede Fahrt nachträglich geändert werden.

## Entitäten

Jede Verbindung erzeugt `sensor.<name>_naechste_fahrt` mit Attribut `journeys` für den nächsten 7-Tage-Zeitraum sowie Binärsensoren für Ausfall und **mögliche** Folgeverspätung. Die Namen vergibt Home Assistant; in Automationen die tatsächliche Entity-ID aus der Oberfläche verwenden.

## Funktionsumfang und Grenzen

- Öffentliche Drittanbieter-Schnittstelle `v6.db.transport.rest`, ohne API-Schlüssel. Kein offizieller DB-SLA. Die Nutzbarkeit und Datenqualität können sich ändern.
- Die Abfahrtsabfrage berücksichtigt explizit die eingetragene Linie und validiert per Trip-Stopps, dass der Zug auch zum Ziel fährt.
- Stündliche Prüfung der sieben kommenden Kalendertage, in den letzten 24 Stunden vor der Fahrt alle 10 Minuten.
- Abfahrts-Ausfallstatus nur dann als **ausgefallen**, wenn die Quelle es ausdrücklich meldet. Nicht gefundene Fahrten werden als `not_found` dargestellt, nicht als Ausfall.
- Vorleistung: Bei **Abfahrt in Göttingen** werden kurz vor Abfahrt Ankünfte derselben Linie gesucht. Das ist **keine bestätigte Fahrzeugumlauf-Kennung** und deshalb nur ein Risikoindikator. Der Puffer ist konfigurierbar.
- Zukunftsprognosen nur, soweit die Datenquelle sie bereits liefert. `scheduled` heißt lediglich, dass keine belastbare Live-Prognose vorliegt.
- Es gibt keine automatische Push-Nachricht; HA-Automationen können die Binärsensoren nutzen.
- Konfiguration: Eine Abfahrtszeit pro Integrationseintrag; drei Fahrten = drei Einträge.
- Für einen Echtbetrieb vor allem Ankunfts-Trip-Matching und Anbieterreaktionen in Home Assistant prüfen.

## Beispiel einer Benachrichtigung

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
      message: "Eine mögliche Vorleistung trifft verspätet in Göttingen ein. Fahrzeugdurchbindung nicht bestätigt."
mode: single
```

Die Entity-ID und den mobilen Notify-Dienst bitte anpassen.
