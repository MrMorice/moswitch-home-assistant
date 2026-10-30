# MoSwitch für Home Assistant

MoSwitch ergänzt einen kapazitiven Zigbee-Raumschalter um automatische
Raumzuordnung, Szenenwechsel und konfigurierbare Doppeltipp-Aktionen in Home
Assistant mit ZHA. Die Integration wird über HACS installiert.

**Stand 0.1.38:** Kompakte Geräteeinstellungen für die ACMP-Firmware ab 0.1.62.
Geprüft mit Home Assistant 2026.10.4. Prüfstand und noch offene Hardwaretests
stehen in den Releaseprotokollen.

## Einrichtung

1. Dieses Repository in HACS als benutzerdefiniertes Repository vom Typ
   **Integration** hinzufügen, herunterladen und Home Assistant neu starten.
2. Den MoSwitch über ZHA anlernen und im normalen HA-Gerätedialog einem Raum
   zuordnen. Die Lampen müssen direkt im selben ZHA-Netz angemeldet sein.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → MoSwitch**
   den Schalter auswählen. Alle physischen Raumlampen werden automatisch ermittelt.
4. Auf der **ZHA-Geräteseite → Geräte-Informationen → Besuchen** den Dialog für
   diesen Schalter öffnen. Die Geräteseite bleibt im Hintergrund geöffnet. Im
   Dialog stehen **Touchfläche** und **Zuordnung & Aktionen** zur Auswahl. Jeder
   Schalter hat seinen eigenen Link und seine eigenen Optionen.
5. Den Raum prüfen, Doppeltipp-Aktion wählen und die Szenenreihenfolge festlegen.
   **Zuordnung speichern** übernimmt Raum und Aktionen zusammen. Bei einem
   Raumwechsel werden Mediengeräte und Szenen für den neuen Raum neu ausgewählt.

Die Szenenreihenfolge gilt für alle MoSwitches im Raum. Raumlampen werden anhand
normaler HA-Raumzuordnungen automatisch ermittelt. Das Panel zeigt diese Leuchten
und kennzeichnet Geräte, die keine direkte ZHA-Steuerung unterstützen. Der Statusring
wird nicht als Lampe angelegt. Einfach-Tipps schalten direkt in der Firmware;
HA sendet dafür keinen zusätzlichen Toggle.

## Touchfläche einlernen

Auf der ZHA-Geräteseite unter **Geräte-Informationen → Besuchen** öffnet sich
MoSwitch direkt für dieses Gerät. Der Dialog enthält **Touchfläche** und
**Zuordnung & Aktionen**. Jeder Schalter hat seine eigenen Einstellungen.

Die endgültige Touchfläche vor dem Einlernen montieren:

1. **Touchfläche einlernen** öffnen, die Fläche vollständig loslassen und
   **Ruhemessung starten** wählen.
2. Etwa vier Sekunden unberührt lassen.
3. Zehnmal kurz tippen und dazwischen vollständig loslassen.
4. Nochmals berühren und halten, bis **Jetzt loslassen** erscheint.
5. Loslassen. Nach erfolgreichem Speichern kehrt Firmware 0.1.62 automatisch
   zum Schalten zurück. **Fertig** schließt den Dialog.

Während des Einlernens sind Schaltaktionen gesperrt. Bei Abbruch oder Fehler
werden bisherige Werte erhalten; eine vorhandene Kalibrierung kann im Dialog
wieder aktiviert werden. Eine laufende Sitzung wird beim Schließen abgebrochen.

Die Geräteseite zeigt **Status**, **Statuslicht**, **Statuslicht Helligkeit** und
**Doppeltippen**. ZHA stellt weiterhin Firmwareupdates und Identifizieren bereit.
Rohmesswert-Sensoren, einzelne Kalibrierungsbuttons und Testmodus-Regler werden
bei der Aktualisierung aus der MoSwitch-Registrierung entfernt. Die Integration
verändert keine fremden Entitäten, Automationen oder Geräteeinstellungen.

Manuelle Touchparameter bleiben im eingeklappten Bereich **Erweiterte
Touch-Einstellungen** verfügbar. Änderungen erfordern eine Speicherbestätigung
des Geräts. Die Helligkeit begrenzt die LED-Ausgabe wie bisher auf 64/255;
100 % im Einsteller entspricht dieser vorgesehenen Obergrenze.

Das HACS-Paket registriert ein Frontend-Modul über HAs Extra-Module und öffnet
den Dialog über den HA-Dialogmanager. Es ersetzt keine HA-Geräteseite.

## Aktivitäten beim Schalter

Ab Version 0.1.6 werden erkannte Gesten im Aktivitätsbereich des bestehenden
ZHA-Geräts protokolliert: **Einfach-Tipp erkannt**, **Doppeltipp erkannt** und
**Halten erkannt**. Firmware ab 0.1.7 meldet auch Einfach-Tipps; ältere Firmware
meldet nur Doppel-/Haltetipps. Ein HACS-Integrationsupdate mit anschließendem
HA-Neustart aktiviert die neue Logbuch-Plattform. Eine erneute Kopplung oder
Raumkonfiguration ist dafür nicht nötig.

Die Ereignisse `moswitch_gesture` tragen `device_id`, `gesture` und eine
Firmware-Sequenznummer. Funkduplikate werden unterdrückt. Aktivitäten werden
auch ohne Raumzuordnung und ohne konfigurierte HA-Aktion aufgezeichnet.
Sie bestätigen die erkannte Geste, keine erfolgreiche Lichtaktion. HA löst
für Einfach-Tipps keinen zusätzlichen Lichtbefehl aus. Recorder und Logbuch
müssen aktiviert sein und dürfen diese Ereignisse nicht per Filter ausschließen.

## Verhalten

- Einfach tippen: aktuellen individuellen Lampenzustand sichern, dann alles
  ausschalten; beim Wiedereinschalten den gemeinsamen Raumzustand wiederherstellen.
- Ohne vollständigen Speicher: normalen Einschaltbefehl an alle Raumlampen senden.
- Doppeltipp: die für diesen Schalter konfigurierte HA-Aktion ausführen.
- Halten: Raumszenen durchschalten; die interne Wiederherstellungsszene gehört
  nie zu diesem Zyklus.

Der direkte Lichtbetrieb soll auch ohne HA und Koordinator arbeiten.
Voraussetzung sind eine erfolgreich eingerichtete Firmware und unterstützte Lampen.
Die native Szenenspeicherung und das Verhalten bei Ausfällen werden am realen
Aufbau verifiziert; Cluster-Unterstützung allein ist noch kein Funktionsnachweis.

## Grenzen und Prüfstand

- Maximal zwölf physische Licht-Endpunkte pro Raum.
- Herstellerprotokoll auf dem Basic-Cluster mit Entwicklungskennung `0xFFF1`;
  es ist noch keine vergebene produktive Herstellerkennung.
- HA-spezifische Integration für 2026.10; keine Zusage für ältere ZHA-APIs.
- Für mehrere Player folgt Play/Pause derzeit der jeweiligen HA-Aktion pro Player.
- Erstinstallation und passender OTA-Bootloader benötigen USB/SWD. Der OTA-Index
  bietet Firmware 0.1.62 für den geprüften Vorgänger 0.1.61 mit
  passendem MoSwitch-Bootloader an. Ein Firmware-Update wird über die native ZHA-Update-Entity
  gestartet; es ist kein HACS-Update und benötigt keinen HA-Neustart.
- Direkte Steuerung und Wiederherstellung wurden mit einem Shelly Dimmer Gen4
  bei physisch abgezogenem Koordinator geprüft. Mehrere Lampen/Schalter sowie
  der montierte Touch- und LED-Aufbau bleiben weitere Hardwaretests.
- Ein vollständiger OTA-Wechsel mit Seitenübertragung und Nachforderung fehlender
  Blöcke wurde bei unveränderter Aufstellung bestätigt (rund 16 Minuten),
  einschließlich Image-Prüfung, Neustart und Konfigurationserhalt. Ein Einfach-Tipp
  kann einen Download kontrolliert abbrechen und direkt das Licht schalten.
- Der Ring zeigt den Gerätestatus. Ein-/Ausschalten und Helligkeit sind konfigurierbar;
  er ist keine Raumleuchte. Keine Cloud und keine hinterlegte HA-IP-Adresse.

## Entwicklung und Prüfung

Python 3.14 verwenden und die Testabhängigkeiten isoliert installieren:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/ruff check custom_components tests tools
.venv/bin/python tools/package.py
```

Das reproduzierbare Integrationspaket und seine Commit-/SHA-256-Metadaten liegen
unter `dist/<version>/`. `AGENTS.md` beschreibt Worktree-, Verifizierungs- und
Releaseablauf. Zugangsdaten und Netzwerkkonfiguration gehören niemals ins Repo.


## Dialog im Browser prüfen

`tools/test_device_dialog.cjs` verwendet die nativen UI-Komponenten einer
verbundenen HA-Instanz in einem isolierten Chrome-Kontext. Alle MoSwitch-Befehle
und Lernmeldungen sind dabei simuliert. Die Zugangsdaten-Datei wird ausschließlich
für die Anmeldung gelesen und darf nicht in Git stehen.

Benötigt werden Node.js, Playwright 1.62.1 und Chrome. Beispiel nach lokaler
Installation von Playwright in einer separaten Testumgebung:

```sh
node tools/test_device_dialog.cjs /absoluter/pfad/zu/ha-zugangsdaten.json
```

Die Datei enthält `url` und `token`. Optional sind `BROWSER_CHANNEL` und
`MOSWITCH_UI_OUTPUT` konfigurierbar. Die Aufzeichnungen liegen standardmäßig unter
`dist/0.1.38/verification/`. Der Test prüft Geräte-Link, unveränderte Geräte-URL,
Zuordnung, alle Lernphasen, Abschluss mit „Fertig“,
Escape, Mobilansicht, Themevariablen und den Schutz vor falscher Gerätezuordnung.
Er ersetzt keinen physischen Einlerndurchlauf.

## Firmwareupdates

Updates laufen über ZHAs vorhandene Firmware-Entität und den öffentlichen
MoSwitch-OTA-Katalog. Private Upload-, Durchsatz-, LED- und Messaufzeichnungs-
Endpunkte sind nicht Teil der Integration. Ältere Entwicklungsstände sind in
den Releaseprotokollen dokumentiert.
