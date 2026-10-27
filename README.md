# MoSwitch für Home Assistant

MoSwitch ergänzt einen kapazitiven Zigbee-Raumschalter um automatische
Raumzuordnung, Szenenwechsel und konfigurierbare Doppeltipp-Aktionen in Home
Assistant mit ZHA. Die Integration wird über HACS installiert.

**Entwicklungsstand 0.1.17:** Integration für die MoSwitch-Bench-Firmware.
Keine fertige Produktfreigabe. Die Integration wird mit Home Assistant 2026.10.2
und ZHA 2.3.1 entwickelt. Raumsteuerung und Firmware müssen zusammen getestet werden.

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

Im Geräte-Dialog unter **Touchfläche** auf **Touchfläche einlernen** drücken. Der neue Ablauf
benötigt Protokoll 7 und eine aktive Touchmessung; aktuell geprüft mit Firmware
0.1.38. Messingscheibe und endgültigen Aufbau vor dem Einlernen montieren.

1. Die Fläche vollständig loslassen und **Ruhemessung starten** wählen.
2. Etwa vier Sekunden unberührt lassen. Der Assistent wechselt danach selbst
   zur Anzeige **0 / 10**.
3. Zehnmal kurz tippen und dazwischen vollständig loslassen. Zähler und Hinweise
   folgen bestätigten Geräteberichten.
4. Nach dem zehnten Tipp nochmals berühren und halten, bis **Jetzt loslassen**
   erscheint. Erst dann den Finger wegnehmen.
5. Das gespeicherte Ergebnis prüfen. **Zum Schalten freigeben** aktiviert den
   Schalter ausdrücklich; **Im stillen Testmodus bleiben** hält Aktionen gesperrt.

Abbruch, Verbindungsabbruch oder Schließen aktivieren keine Schaltaktionen.
Eine von dieser Seite gestartete laufende Lernsitzung wird beim Schließen
abgebrochen. Der stille Diagnosemodus kann ebenfalls hier eingeschaltet werden;
eine vorhandene gültige Kalibrierung lässt sich ausdrücklich wieder freigeben.

Die normale Geräteseite zeigt wieder native Konfigurations-Entitäten: Baseline,
Ein-/Loslassabstand, Touch-Testmodus und Doppeltipp-Verhalten. Unter Diagnose stehen
Touchstatus, Kontakt, Lernfortschritt, Ruhewert und Ruheschwankung. Mediengeräte,
Szenenreihenfolge und eigene Aktionen werden im Reiter **Zuordnung & Aktionen**
zugewiesen. Die normale HA-Raumzuordnung bleibt ebenfalls verwendbar.

Zusätzliche Messwerte, Timing-Parameter und alte einzelne Lern-Buttons bleiben
standardmäßig deaktiviert. Alle Parameter sind weiterhin im eingeklappten Bereich
**Diagnose und Feineinstellung** des Dialogs verfügbar. Bei der Migration werden
nur zuvor durch die Integration deaktivierte wichtige Controls wieder aktiviert;
Benutzer-Deaktivierungen und spätere manuelle Entscheidungen bleiben erhalten.
ZHA-Firmwareupdate und Identifizieren werden nicht geändert.

Das HACS-Paket registriert ein eigenes Frontend-Modul über HAs Extra-Module und
öffnet den Dialog über den HA-Dialogmanager. Es verändert keine HA-Komponenten
oder Geräteseiten-Templates. Browser Mod und Dashboard-Karten werden nicht
benötigt. Der Einstieg ist der vorhandene Geräte-Link **Besuchen** unter
Geräte-Informationen. Es wird kein zusätzlicher Button in die Konfigurationskarte
injiziert. Nach dem Update den Browser vollständig neu laden, damit das Modul
auch auf der Geräteseite geladen ist. Alte Links auf `/moswitch/<entry_id>` und
die nativen MoSwitch-Optionsdialoge bleiben als Rückfallweg verfügbar.

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

## Grenzen dieser Testversion

- Maximal zwölf physische Licht-Endpunkte pro Raum.
- Herstellerprotokoll auf dem Basic-Cluster mit Entwicklungskennung `0xFFF1`;
  es ist noch keine vergebene produktive Herstellerkennung.
- HA-spezifische Integration für 2026.10; keine Zusage für ältere ZHA-APIs.
- Für mehrere Player folgt Play/Pause derzeit der jeweiligen HA-Aktion pro Player.
- Erstinstallation und passender OTA-Bootloader benötigen USB/SWD. Der OTA-Index
  enthält Firmware-Prototyp 0.1.4 ausschließlich für Firmware 0.1.2/0.1.3 mit
  passendem MoSwitch-Bootloader. Ein Firmware-Update wird über die native ZHA-Update-Entity
  gestartet; es ist kein HACS-Update und benötigt keinen HA-Neustart.
- Direkte Steuerung und Wiederherstellung wurden mit einem Shelly Dimmer Gen4
  bei physisch abgezogenem Koordinator geprüft. Mehrere Lampen/Schalter sowie
  der montierte Touch- und LED-Aufbau bleiben weitere Hardwaretests.
- Ein vollständiger OTA-Wechsel mit Seitenübertragung und Nachforderung fehlender
  Blöcke wurde bei unveränderter Aufstellung bestätigt (rund 16 Minuten),
  einschließlich Image-Prüfung, Neustart und Konfigurationserhalt. Ein Einfach-Tipp
  kann einen Download kontrolliert abbrechen und direkt das Licht schalten.
- Keine Fernsteuerung des Rings, keine Cloud und keine in der Firmware
  hinterlegte HA-IP-Adresse.

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


Ab Integration 0.1.8 und Firmware-Prototyp 0.1.13 zeigt „Touchkontakt“ den
bestätigten Kontaktbeginn und das Loslassen direkt am bestehenden ZHA-Gerät.
Die Kalibrierungsanzeige unterscheidet „Kontakt erkannt“, „Tipp gezählt“ und
sichtbare Ablehnungsgründe (zu lang, zu schwach, Messaussetzer). Jeder erkannte
Lernkontakt und sein Ergebnis erscheinen zusätzlich im Aktivitätsbereich als
Kalibrierungsereignis, ohne normale Schaltaktionen auszulösen. Das Zählen erfolgt
erst nach Loslassen. Zulässige Lernkontakte dauern jetzt bis zu zwei Sekunden.
Das Akzeptieren ungültiger Messungen oder eine dauernde Driftkalibrierung ist
damit nicht verbunden. Ein Netzstörungsproblem kann weiter Kontakte verhindern;
die Oberfläche benennt es jetzt. Ältere Statusprotokolle bleiben lesbar.

Ab Integration 0.1.9 sind Touch-Messwert, gespeicherte Baseline, absolute
Einschalt-/Loslassschwellen, letzte Lernkontaktdauer und verworfene Lernkontakte
als eigene Sensoren sichtbar. Unter Konfiguration stehen Baseline sowie
Einschalt- und Loslassabstand als ganzzahlige µs-Felder. Eine größere
Einschaltdifferenz macht den Touch weniger empfindlich. Der Loslassabstand
muss kleiner bleiben; zum Loslassen muss der Messwert entsprechend sinken.
Die absoluten Schwellen sind jeweils Baseline plus Abstand.

Schreiben erfordert Firmware 0.1.14; bei älterer Firmware bleiben die Einsteller
nicht verfügbar, die vorhandenen Messwerte bleiben lesbar. Während Kalibrierung
oder Berührung werden Änderungen abgelehnt. Werte werden gemeinsam geprüft,
im EEPROM-Journal gespeichert und zurückgelesen. HA zeigt keinen optimistischen
Erfolg, sondern verlangt die Rückmeldung des gespeicherten Tupels. Ein ACK ohne
Wertebestätigung reicht nicht. Die angezeigten Sensorwerte folgen Statusberichten;
zusätzliche Sensoren erzeugen keine eigenen Funkabfragen.

Ab Integration **0.1.10** mit Firmware **0.1.18**: „Touch-Testmodus“ erlaubt
Diagnose auch bei gehaltener Scheibe. Alle normalen Schalt-/Szenen-/Medienaktionen
bleiben im Testmodus gesperrt, auch nach Lernfehler oder Abbruch. Zum Einlernen
„Touch kalibrieren“, vollständig loslassen und „Touch-Ruhemessung starten“.
Vier Sekunden unberührt lassen, dann zehnmal tippen. Anschließend Testmodus
explizit ausschalten. Acht Zahlenfelder enthalten drei µs-Schwellwerte und fünf
Filter-/Zeitparameter; Rohwert, gefilterter Wert und bestätigter Touchkontakt
werden getrennt angezeigt. Die Aussetzer-Toleranz ist begrenzt auf 0–30 ms;
ungültige Messungen können keinen neuen Kontakt bestätigen. Medianfenster nur
1/3/5/7. Änderungen werden erst nach Speicherbestätigung des Geräts übernommen.

Ab Integration **0.1.13** mit Firmware **0.1.24** führt **Einstellungen → Geräte &
Dienste → MoSwitch → Konfigurieren → Touchfläche einlernen** durch die gesamte
Kalibrierung. Zuerst vorbereiten, dann bei losgelassener Fläche die viersekündige
Ruhemessung bestätigen. Der Dialog zeigt den Lernfortschritt, Ablehnungsgründe
und die vom Gerät gemeldete Restzeit. Vor dem ersten Kontakt läuft kein Zeitlimit;
danach erneuert jeder erkannte Kontakt die 90 Sekunden Inaktivitätsfrist.

Erst nach zehn gespeicherten Tipps bietet der Dialog die ausdrückliche Freigabe
zum Schalten an. Alternativ bleibt der stille Testmodus aktiv. Schließen während
der Mess-/Lernphase sendet Abbruch; der Testmodus bleibt auch bei Funkfehlern
aktiv. Vor dem Start der Ruhemessung bleibt beim Schließen die stille Vorbereitung
bestehen. Die manuellen Buttons und Parameter bleiben verfügbar, ältere
Statusprotokolle weiterhin lesbar. Zwei zusätzliche Sensoren zeigen gezählte
Lerntipps und die Inaktivitätsrestzeit; vor dem ersten Tipp ist die Restzeit 0
(Frist noch nicht gestartet). Der geführte Dialog verlangt Protokoll 6, damit er
keine falschen Versprechen über das Zeitlimit älterer Firmware macht.

Ab **0.1.14** enthält jedes zugeordnete ZHA-Gerät einen internen Konfigurationslink
zur MoSwitch-Integration mit seiner eigenen Eintrags-ID. Auf der Geräteseite über
„Konfiguration öffnen“ / „Besuchen“ (je nach HA-Frontend auch im Gerätemenü)
öffnen: HA hebt dort genau diesen Schalter hervor. Sein Zahnrad öffnet den
Assistenten. Der Link öffnet keinen Optionsdialog automatisch. Auch bei mehreren
Schaltern bleiben Zuordnung, Lernzustand und Parameter pro Eintrag getrennt.
Dialogtitel zeigen den aktuellen Gerätenamen; benutzerdefinierte Namen haben
Vorrang. Das ZHA-Gerät, seine Besitzer-Integration, Raumzuordnung und Entitäten
bleiben erhalten. Beim Löschen des MoSwitch-Eintrags wird dessen eigener Link
entfernt; beim bloßen Neuladen bleibt er verfügbar.

## ACMP-Assistent mit Haltetest (0.1.15)

Mit ACMP-Firmware ab0.1.35 führt **Touchfläche einlernen** durch Ruhephase,
zehn kurze Tipps und einen zweisekündigen Halte-/Loslasstest. Nach dem zehnten
Tipp der jeweiligen Anweisung im Dialog folgen; die Werte sind dann noch nicht
gespeichert. Erst nach erfolgreichem Haltetest und stabilem Loslassen werden sie
übernommen. Der Schalter bleibt still, bis er im Ergebnis ausdrücklich zum
Schalten freigegeben wird. Der Assistent zeigt den Namen des ausgewählten
Schalters, Ruhewert, Ruheschwankung und gelernte Abstände. Für die endgültige
Kalibrierung Scheibe/Abdeckung montieren und die vorgesehene Versorgung nutzen.
Bestehende Geräte mit älterer Firmware bleiben lesbar; der neue Ablauf fordert
die passende Firmware an. Die ACMP-Kalibrierung liegt getrennt von GPIO-Werten.


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
`dist/0.1.17/verification/`. Der Test prüft Geräte-Link, unveränderte Geräte-URL,
Zuordnung, alle Lernphasen, Beenden im stillen Modus, ausdrückliche Freigabe,
Escape, Mobilansicht, Themevariablen und den Schutz vor falscher Gerätezuordnung.
Er ersetzt keinen physischen Einlerndurchlauf.
