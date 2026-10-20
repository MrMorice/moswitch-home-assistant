# Arbeitsregeln für die MoSwitch-HA-Integration

Diese Regeln gelten für das gesamte HA-Repository und seine Worktrees.

## Feature-Workflow

1. Neue Features immer in einem eigenen Git-Worktree mit eigenem Branch auf
   Basis von `main` entwickeln. Keine Feature-Entwicklung direkt auf `main`.
   Vor dem Abzweigen den aktuellen Stand von `main` verwenden; bei vorhandenem
   Remote zuerst fetchen und `main` ausschließlich per Fast-Forward aktualisieren.
   Vorhandene lokale Änderungen nicht überschreiben.
2. Worktrees im ignorierten Verzeichnis `.worktrees/` des Haupt-Checkouts
   anlegen. Beispiel aus dem Haupt-Checkout:
   `git worktree add -b feature/<name> .worktrees/<name> main`.
3. Das Feature im Worktree implementieren und sinnvoll committen. Vor dem Merge
   die für die Änderung relevanten Tests, statischen Prüfungen und den
   Integrationspaket-Build ausführen. Änderungen an Pins, Peripherie, Timing oder Funk
   zusätzlich auf der Zielplatine prüfen, soweit für ihre Verifizierung nötig.
   Prüfbefehle, Ergebnisse und Hardwarestand dokumentieren. Nicht ausgeführte
   Prüfungen ausdrücklich als offen kennzeichnen; sie gelten nicht als bestanden.
4. Erst nach erfolgreicher Verifizierung nach `main` mergen. Falls `main` seit
   dem Abzweigen geändert wurde, den aktuellen Stand in den Feature-Branch
   integrieren und die betroffenen Prüfungen erneut ausführen. Den Merge im
   sauberen Haupt-Checkout mit `git merge --no-ff feature/<name>` ausführen.
5. Nach jedem Feature-Merge einen neuen Release-Build aus dem sauberen,
   zusammengeführten `main` erstellen. Keine Artefakte aus dem Feature-Worktree
   als Release des Merge-Stands übernehmen. Den Release-Build prüfen und die
   tatsächlich gebauten Integrationspakete aufbewahren.
6. Jeden Release mit eindeutiger, aufsteigender SemVer-Version und Git-Tag
   `v<major>.<minor>.<patch>` auf dem gebauten Commit kennzeichnen. Änderungen,
   Commit-SHA, HA-/ZHA-Version, Build-Befehl, Zielplatine, Prüfergebnisse
   und Artefaktpfade in `releases/<version>.md` dokumentieren. Artefakte unter
   `dist/<version>/` ablegen; generierte Binärdateien nicht in Git einchecken.
   Den Tag erst nach erfolgreichem Release-Build setzen. Keine bestehenden
   Release-Tags verschieben oder überschreiben.
7. Nach erfolgreichem Merge und Release den sauberen Feature-Worktree entfernen
   und den vollständig gemergten Feature-Branch löschen. Keine uncommitteten
   Änderungen verwerfen.

## Build- und Verifizierungsgrundlage

- Beim Einrichten der Firmware verbindliche, reproduzierbare Befehle für Setup,
  Tests und Release-Build sowie die benötigten HA-/ZHA-Versionen in
  `README.md` dokumentieren. Diese Befehle anschließend im Workflow verwenden.
- Solange Toolchain, Integration oder notwendige Zielhardware-Prüfungen fehlen,
  keinen erfolgreichen Integrationspaket-Build oder verifizierten Release behaupten.
  Fehlende Voraussetzungen und offene Prüfungen konkret melden.
- Ein Feature ist erst abgeschlossen, wenn Verifizierung, Merge nach `main`
  und der neue Release-Build erfolgreich durchgeführt und dokumentiert sind.
- Push, Veröffentlichung eines Releases und Installation in einer laufenden
  HA-Instanz nur im Rahmen des vom Nutzer beauftragten Arbeitsumfangs durchführen.
