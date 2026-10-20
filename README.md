# MoSwitch für Home Assistant

MoSwitch ergänzt einen kapazitiven Zigbee-Raumschalter um automatische
Raumzuordnung, Szenenwechsel und konfigurierbare Doppeltipp-Aktionen in Home
Assistant mit ZHA. Die Integration wird über HACS installiert.

**Entwicklungsstand 0.1.0:** Erste Testversion für die MoSwitch-Bench-Firmware.
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
- Die Firmware-Bench-Version benötigt USB zum Flashen. Zigbee-OTA ist ein
  separates, noch nicht fertiggestelltes Firmware-Arbeitspaket.
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
