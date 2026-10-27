"""Guided learning using confirmed device reports; closing keeps actions blocked."""

import asyncio
import time

import probatio as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zigpy.exceptions import ZigbeeException

from .const import DOMAIN, TOUCH_FAILURES, TOUCH_FEEDBACK
from .device_configuration import device_name

COMMAND_ERRORS = (HomeAssistantError, ZigbeeException, TimeoutError, ValueError, KeyError, RuntimeError)


def learning_message(status):
    """Instructions for the current device-confirmed stage."""
    state = status.get("state")
    if state in (1, 2):
        return "Fläche unberührt lassen. Die Ruhemessung dauert insgesamt etwa vier Sekunden."
    if state == 7:
        count = status.get("learning_taps", 0)
        feedback = TOUCH_FEEDBACK.get(status.get("feedback"), "Bitte kurz tippen und loslassen")
        if not status.get("detected_contacts"):
            clock = "Die LED pulsiert. Du kannst dir vor dem ersten Tipp Zeit lassen."
        else:
            clock = (f"Noch {status.get('learning_remaining_s', 0)} Sekunden bis zum Abbruch bei Inaktivität. "
                     "Jeder erkannte Kontakt erneuert die Frist.")
        signal = (f"Ruhewert: {status.get('learning_baseline_us', '–')} µs; "
                  f"Ruheschwankung: {status.get('idle_noise_us', '–')} µs.")
        return f"**{count} von 10 Tipps gezählt**\n\n{feedback}.\n\n{clock}\n\n{signal}\n\nZwischen den Tipps vollständig loslassen."
    if state == 11:
        return "**10 von 10 Tipps erfasst – noch nicht gespeichert.**\n\nZuerst kurz vollständig loslassen. Dann die Fläche einmal berühren und mindestens zwei Sekunden halten. Die LED leuchtet bei erkanntem Kontakt. Erst loslassen, wenn hier die Aufforderung erscheint."
    if state == 12:
        seconds = status.get("hold_remaining_ms", 2000) / 1000
        return f"**Haltetest läuft: noch etwa {seconds:.1f} Sekunden.**\n\nFinger liegen lassen. Der Schalter prüft, ob der Kontakt ohne Unterbrechung erkannt wird."
    if state == 13:
        return "**Haltetest bestanden – jetzt vollständig loslassen.**\n\nNach einer stabilen Ruhephase speichert der Schalter die neuen Werte. Schaltaktionen bleiben gesperrt."
    if state == 3:
        return ("**Einlernen erfolgreich abgeschlossen und gespeichert.**\n\n"
                f"Ruhewert: {status.get('baseline_us', '–')} µs; "
                f"Einschaltabstand: {status.get('press_delta_us', '–')} µs; "
                f"Loslassabstand: {status.get('release_delta_us', '–')} µs.\n\n"
                "Du kannst den Schalter jetzt zum Schalten freigeben oder im stillen Testmodus bleiben.")
    reason = TOUCH_FAILURES.get(status.get("failure_reason"))
    if state == 4:
        return f"Einlernen beendet: {reason or 'Kalibrierung fehlgeschlagen'}. Die bisherigen Werte bleiben erhalten."
    if state == 8:
        return "Einlernen abgebrochen. Die bisherigen Werte bleiben erhalten."
    return "Einlernen nicht abgeschlossen. Bitte den Touchstatus und die Verbindung prüfen."


async def confirmed_touch_command(hass, entry, method, expected, *args):
    """Require a fresh report after the command, rather than treating its ACK as state."""
    runtime = entry.runtime_data
    changed = asyncio.Event()
    unsub = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}", changed.set,
    )
    before = time.monotonic()
    try:
        await method(entry, *args)
        async with asyncio.timeout(15):
            while True:
                changed.clear()
                if (runtime.touch_received_at.get(entry.entry_id, 0) > before
                        and expected(runtime.touch_status.get(entry.entry_id, {}))):
                    return
                await changed.wait()
    finally:
        unsub()


class TouchWizard:
    """Options-flow steps; no action is enabled without explicit confirmation."""

    def _touch_placeholders(self, **extra):
        return {"device": device_name(self.hass, self.config_entry), **extra}

    def _touch_runtime(self):
        return self.config_entry.runtime_data

    def _touch_status(self):
        return self._touch_runtime().touch_status.get(self.config_entry.entry_id, {})

    async def _touch_command(self, method, expected, *args):
        await confirmed_touch_command(self.hass, self.config_entry, method, expected, *args)

    async def async_step_touch_setup(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._touch_command(
                    self._touch_runtime().query_touch, lambda s: True,
                )
                if self._touch_status().get("protocol_version", 0) < 7:
                    errors["base"] = "touch_upgrade"
                elif not self._touch_status().get("frontend_active"):
                    errors["base"] = "touch_connection"
                else:
                    await self._touch_command(
                        self._touch_runtime().calibrate_touch, lambda s: s.get("state") == 9,
                    )
                    return await self.async_step_touch_baseline()
            except COMMAND_ERRORS:
                errors["base"] = "touch_connection"
        return self.async_show_form(step_id="touch_setup", data_schema=vol.Schema({}), errors=errors,
                                    description_placeholders=self._touch_placeholders())

    async def async_step_touch_baseline(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._touch_command(
                    self._touch_runtime().baseline_touch,
                    lambda s: s.get("state") in (1, 2, 7, 4, 5, 6),
                )
                self._touch_error = None
                self._touch_task = None
                return await self.async_step_touch_watch()
            except COMMAND_ERRORS:
                errors["base"] = "touch_connection"
        return self.async_show_form(step_id="touch_baseline", data_schema=vol.Schema({}), errors=errors,
                                    description_placeholders=self._touch_placeholders())

    async def _watch_touch(self):
        changed = asyncio.Event()
        unsub = async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.config_entry.entry_id}", changed.set,
        )
        previous = None
        try:
            while True:
                changed.clear()
                status = self._touch_status()
                state = status.get("state")
                progress = (status.get("learning_taps", 0) / 10) * 0.75
                if state in (11, 12, 13):
                    progress = 0.75 + (1 - status.get("hold_remaining_ms", 2000) / 2000) * 0.2
                if state == 3:
                    progress = 1
                self.async_update_progress(progress)
                signature = tuple(status.get(k) for k in (
                    "state", "feedback", "learning_taps", "learning_remaining_s", "detected_contacts", "hold_remaining_ms",
                ))
                if signature != previous:
                    previous = signature
                    self.async_notify_flow_changed()
                if status.get("state") not in (1, 2, 7, 11, 12, 13):
                    return
                await asyncio.wait_for(changed.wait(), timeout=120)
        except TimeoutError:
            self._touch_error = "Keine aktuellen Meldungen vom Schalter. Die Verbindung prüfen."
        except asyncio.CancelledError:
            # Closing the wizard must not leave a running learning session behind.
            try:
                await self._touch_runtime().cancel_touch(self.config_entry)
            except COMMAND_ERRORS:
                pass
            raise
        finally:
            unsub()

    async def async_step_touch_watch(self, user_input=None):
        if self._touch_task is None:
            self._touch_task = self.hass.async_create_task(self._watch_touch())
        if self._touch_task.done():
            return self.async_show_progress_done(next_step_id="touch_result")
        return self.async_show_progress(
            step_id="touch_watch", progress_action="touch_learning",
            description_placeholders=self._touch_placeholders(status=learning_message(self._touch_status())),
            progress_task=self._touch_task,
        )

    async def async_step_touch_result(self, user_input=None):
        status = self._touch_status()
        success = status.get("state") == 3 and not self._touch_error
        errors = {}
        if user_input is not None:
            action = user_input["action"]
            if action == "retry":
                return await self.async_step_touch_setup()
            if action == "activate":
                try:
                    await self._touch_command(
                        self._touch_runtime().test_touch,
                        lambda s: not s.get("test_mode") and s.get("state") == 3,
                        False,
                    )
                except COMMAND_ERRORS:
                    errors["base"] = "touch_connection"
            if not errors:
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
        choices = (["activate"] if success else []) + ["retry", "keep_test"]
        return self.async_show_form(
            step_id="touch_result", errors=errors,
            description_placeholders=self._touch_placeholders(status=self._touch_error or learning_message(status)),
            data_schema=vol.Schema({vol.Required("action", default=choices[0]): selector.SelectSelector({
                "options": choices, "translation_key": "touch_finish",
            })}),
        )
