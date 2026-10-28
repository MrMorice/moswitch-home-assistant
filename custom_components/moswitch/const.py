"""Shared MoSwitch integration constants."""

DOMAIN = "moswitch"
ENDPOINT = 10
MANUFACTURER = 0xFFF1
RESTORE_SCENE = 254
MAX_TARGETS = 12
IGNORE_LABEL = "MoSwitch ignorieren"

TOUCH_FEEDBACK = {
    1: "Bitte vollständig loslassen",
    2: "Bereit für den nächsten Tipp",
    3: "Kontakt erkannt – bitte loslassen",
    4: "Tipp gezählt",
    5: "Nicht gezählt: Kontakt zu kurz",
    6: "Nicht gezählt: länger als 2 Sekunden",
    7: "Nicht gezählt: Signal zu schwach",
    8: "Nicht gezählt: Messaussetzer",
    9: "Jetzt zwei Sekunden halten",
    10: "Kontakt halten",
    11: "Jetzt vollständig loslassen",
}

TOUCH_FAILURES = {
    1: "anhaltend ungültige Messwerte",
    2: "Abtastpause",
    3: "zu wenige Ruhemessungen",
    4: "Ruhemessung zu unruhig",
    5: "ungültige Kalibrierwerte",
    6: "Zeitlimit erreicht",
    7: "Haltetest unterbrochen",
}
