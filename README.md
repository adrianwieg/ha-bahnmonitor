# Bahnmonitor – Home Assistant (v0.1.3, Prototyp)

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

## Ausfallsicherheit

Bei HTTP 502/503/504 bzw. einem vorübergehenden Verbindungsproblem bleibt die Integration eingerichtet und lauffähig. Nach einer fehlgeschlagenen Anfrage pausiert sie weitere API-Anfragen und versucht es nach 15, 30, 60 beziehungsweise maximal 120 Minuten erneut. Während einer Störung meldet der Sensor **Fahrplandienst** den Status `unavailable`, die nächste Fahrt **Datenquelle nicht erreichbar**. Vorhandene Daten werden als `stale` gekennzeichnet, und die Binärsensoren für Ausfall und mögliche Folgeverspätung werden nicht als aktuelle Information angeboten. **Diese Änderung repariert nicht den externen 503-Dienst**; echte Zugdaten erscheinen erst wieder, wenn der Anbieter antwortet.

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
