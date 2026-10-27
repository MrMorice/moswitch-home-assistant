# LED-Diagnose: vorbereiteter Featurestand0.1.21

Die Vorbereitung ist durch den Nutzer zur Installation freigegeben; eine
Hardwareabnahme der LED-Ausgabe steht weiterhin aus. Ausgangsstand vor diesem
Installationsauftrag: HA0.1.20 und Platine0.1.42. Der vorhandene
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

## Korrekturfreigabe 0.1.22

Nach dem ersten Enable von Firmware0.1.44 fiel die Gerätekommunikation aus.
Der Ring-Farbtest wurde nicht gestartet. Nach manuellem Neustart antwortet das
Gerät wieder. Die interne, gerätebezogene Uploadfreigabe wechselt ausschließlich
auf den stillen Korrekturkandidaten0.1.45 aus Gerätestand0.1.44, SHA-256
`e840dc3e6680f9d2ec9ff4f998b3281d15fdc13cdb490f0ed608f2b11995b426`,
361402Bytes. Dieser begrenzt die MG24-EUSART-Initialisierung/Abschaltung und
wartet beim TX-only-Start nicht auf RXIDLE. Zwölf native Firmwaretests und der
saubere MG24-Build bestanden; die physische Korrekturprüfung steht noch aus.
Der HA-Upload prüft weiterhin exakte Dateiidentität, Quellversion und frisch
bestätigten stillen Diagnosestatus. Es gibt keine generelle Firmwarefreigabe.

## Folgekorrektur 0.1.23

Firmware0.1.45 wurde installiert. Der LED-Start antwortet mit Fehler7;
Touchdiagnose und Funk bleiben frisch erreichbar. Der tatsächliche MG24-CMU-Mux
unterstützt für EUSART0 die Taktgruppe C, nicht den bisher gewählten Wert A.
Der festgelegte0.1.46-Build korrigiert dies, prüft Auswahl und Referenzfrequenz
und behält die begrenzten Wartepfade bei. Interner Upload ausschließlich von
0.1.45, Größe 361434, SHA-256 `d8acb7bb92b280b532f99e9fa57908eb653cf0f640205192176f520eb3a5780c`.
Keine allgemeine Firmwarefreigabe und keine neue Licht- oder Raumaktion.
Physische LED-Prüfung weiter offen.
