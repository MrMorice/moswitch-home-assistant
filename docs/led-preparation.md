# LED-Diagnose: vorbereiteter Featurestand0.1.21

Noch nicht installiert, veröffentlicht oder als Hardwarefunktion abgenommen.
Die laufende Integration bleibt0.1.20 und die Platine0.1.42. Der vorhandene
Einlernlauf wurde während der Abwesenheit des Nutzers still beendet; die spätere
Antwort zur alten Testfrage wird nicht als neuer bestandener Durchlauf gewertet.

## Bedienung für den späteren gemeinsamen Test

WebSocket `moswitch/led/action` mit `entry_id`, `action` und optional `value`.
Nur Administratoren und das zugeordnete MoSwitch-Gerät. Reihenfolge bei sicher
vorhandener5V_LED-Versorgung: status -> confirm_supply -> enable -> test.
`confirm_supply` ist eine ausdrückliche Bedieneraussage, keine Messung oder
USB-Erkennung. Sie schaltet allein noch keine Daten-/LED-Ausgabe ein. Der
begrenzte Test zeigt Rot/Grün/Blau und die acht LED-Indizes. Die Helligkeit ist
als Benchwert auf0..64 begrenzt (32 beim Start). Kein Ring-light, keine Automation
und keine Änderungen an Raum-/Licht-/Szenenaktionen.

`disable` fordert einen Schwarz-Frame an und fragt nach, bis die Ressourcen-
freigabe vom Gerät bestätigt ist. Bei Treiberfehlern wird kein erfolgreicher
Abschluss behauptet. `forget_supply` beendet die Datenansteuerung unmittelbar;
bei weiter versorgten LEDs kann die zuletzt gelatchte Farbe dabei erhalten
bleiben. Vor einem MCU-Update den Ring regulär deaktivieren. Ein Neustart
widerruft die Bestätigung auf dem Gerät und startet die Ausgabe nicht neu.

## Protokoll und Grenzen

Basic-Cluster0xfff1, Endpunkt10, Befehl0x4d mit Operation/Wert/Nonce. Es wird nur
auf die validierte0x83-Geräteantwort gewartet, nicht auf eine ZCL-DefaultResponse.
IEEE, zufällige Nonce und Operation müssen übereinstimmen. Gleichzeitige Anfragen
werden pro Gerät serialisiert. Future/Lock-Zustände werden bei Fehler/Timeout
bereinigt. Flags und Fehlercodes beschreiben den Controllerzustand; sie beweisen
keine reale LED-Farbe oder vorhandene Versorgung.

Der nur lokal vorbereitete Upload ist per SHA-256 auf den gemeinsamen
Firmwarekandidaten0.1.44 aus Gerätestand0.1.42 festgelegt. Der enthält den letzten
Touch-Code einschließlich Messlücken-Korrektur und separat gespeicherter
Q1/Q2-Kalibrierung. Lichtaktionen bleiben gesperrt und der LED-Ring deaktiviert,
bis er bewusst bestätigt/aktiviert wird. Der0.1.43-Zwischenbuild nur des LED-
Featurebranches ist keine geeignete Aktualisierung des laufenden Touch-Kandidaten.

64 pytest-Tests und Ruff prüfen Protokollgrenzen, Administratorrechte, falsche/
alte/fremde Antworten, explizite Bestätigung, Aktivierung/Test/Helligkeit,
Abschaltbestätigung und Fehlerbereinigung. Geräte-/Browserabnahme dieser neuen
Befehle wurde nicht durchgeführt. Die Oberfläche wurde nicht verändert.
Firmwaredetails und offene Abnahme stehen im Firmware-Integrationsworktree unter
`docs/led-control.md`.
