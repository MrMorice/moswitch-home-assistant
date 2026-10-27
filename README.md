# MoSwitch für Home Assistant

MoSwitch ergänzt einen kapazitiven Zigbee-Raumschalter um automatische
Raumzuordnung, Szenenwechsel und konfigurierbare Doppeltipp-Aktionen in Home
Assistant mit ZHA. Die Integration wird über HACS installiert.

**Entwicklungsstand 0.1.7:** Erste Testversion für die MoSwitch-Bench-Firmware.
Keine fertige Produktfreigabe. Die Integration wird mit Home Assistant 2026.10.2
und ZHA 2.3.1 entwickelt. Raumsteuerung und Firmware müssen zusammen getestet werden.

## Einrichtung

1. Dieses Repository in HACS als benutzerdefiniertes Repository vom Typ
   **Integration** hinzufügen, herunterladen und Home Assistant neu starten.
2. Den MoSwitch über ZHA anlernen und im normalen HA-Gerätedialog einem Raum
   zuordnen. Die Lampen müssen direkt im selben ZHA-Netz angemeldet sein.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → MoSwitch**
   den Schalter auswählen. Alle physischen Raumlampen werden automatisch ermittelt.
4. In den Optionen der MoSwitch-Integration die Doppeltipp-Aktion wählen:
   Play/Pause mit einem oder mehreren Raumplayern, eigene HA-Aktion oder keine Aktion.
5. Die Raumszenen in gewünschter Reihenfolge auswählen. Szenen mit dem von der
   Integration angelegten Label **MoSwitch ignorieren** werden übersprungen.

Die Szenenreihenfolge gilt für alle MoSwitches im Raum. Ein Diagnose-Sensor zeigt
an, ob die technische Einrichtung erfolgreich war. Der Statusring wird nicht als
Lampe in HA angelegt. Raumänderungen werden ohne erneutes Kompilieren der
Firmware erkannt. Reines Lichtschalten erfolgt auf dem Schalter, niemals durch
zusätzliche HA-Toggle-Automationen.

## Touch kalibrieren

Ab Version 0.1.7 gibt es beim vorhandenen ZHA-Schalter unter Konfiguration
**Touch kalibrieren** und **Kalibrierung abbrechen**. Der Diagnosesensor
**Touchkalibrierung** zeigt den Ablauf und „Bitte tippen: n/10“.
Firmware-Diagnosestand ab 0.1.8 ist für das geführte Lernen erforderlich.

1. Den Schalter unberührt auf seiner üblichen Unterlage lassen und den
   Kalibrierknopf drücken. Vier Sekunden nicht berühren.
2. Sobald „Bitte tippen: 0/10“ erscheint, zehnmal wie im Alltag kurz tippen,
   jeweils vollständig loslassen und etwa 0,3–1 Sekunden Abstand lassen.
3. Jeder vollständig erkannte Tipp erhöht den Zähler. Die einfarbige
   XIAO-Onboard-LED atmet während des Lernens und leuchtet bei Kontakt auf.
4. Erst „Kalibriert“ bestätigt das gespeicherte Ergebnis. Bis dahin sind
   normale Licht-/Mediengesten gesperrt. Nach 90 Sekunden ohne Abschluss
   oder beim Abbrechen bleibt die vorige Kalibrierung erhalten.

Mit Messingscheibe und Gehäuse erneut lernen. Blaues Ringfeedback ist Teil
der späteren Ring-Inbetriebnahme; im derzeitigen USB-Aufbau wird die
Onboard-LED verwendet. Der Status zeigt Rohwerte, Schwellen und schwächsten
Lerntipp als Attribute. Die Entities werden direkt dem ausgewählten bestehenden
ZHA-Gerät zugeordnet. Ab Firmware 0.1.5 bleiben alte Ruhe-Kalibrierungen lesbar;
der Zehn-Tipp-Modus erfordert die neue Firmware.

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
