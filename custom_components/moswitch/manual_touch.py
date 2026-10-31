"""Manual touch adjustment using existing quiet-mode and parameter commands."""

import asyncio

from homeassistant.exceptions import HomeAssistantError

from . import protocol
from .touch_wizard import COMMAND_ERRORS, confirmed_touch_command

ACTIVE_STATES = (1, 2, 7, 9, 11, 12, 13)
PROFILE_FIELDS = ("baseline_us", "press_delta_us", "release_delta_us")
MANUAL_FIELDS = (*PROFILE_FIELDS, *protocol.TUNING_FIELDS)


def initial_values(status):
    """Return editable suggestions; creating a draft does not write the device."""
    baseline = next((status.get(k) for k in ("baseline_us", "filtered_us", "raw_us", "learning_baseline_us")
                     if isinstance(status.get(k), int) and 1 <= status[k] <= 1000), 20)
    press = status.get("press_delta_us") or max(4, (status.get("idle_noise_us") or 1) * 2 + 2)
    release = status.get("release_delta_us") or max(1, press // 2)
    return {"baseline_us": baseline, "press_delta_us": press, "release_delta_us": release,
            **{key: status.get(key, default) for key, default in zip(
                protocol.TUNING_FIELDS, (8, 12, 3, 50, 100), strict=True)}}


def validate_values(values):
    if set(values) != set(MANUAL_FIELDS):
        raise ValueError("Bitte alle acht Touchparameter angeben")
    if not protocol.valid_touch_parameters(*(values[k] for k in PROFILE_FIELDS)):
        raise ValueError("Loslassabstand muss kleiner als Einschaltabstand sein")
    if not protocol.valid_touch_tuning(*(values[k] for k in protocol.TUNING_FIELDS)):
        raise ValueError("Ungültige Entprellung oder Filtereinstellung")


async def pause_for_adjustment(hass, entry):
    """Stop automatic learning and require a physical quiet-mode confirmation."""
    runtime = entry.runtime_data
    if runtime.is_updating(entry):
        raise HomeAssistantError("Bitte das Firmwareupdate abwarten")
    await confirmed_touch_command(hass, entry, runtime.query_touch, lambda s: True)
    status = runtime.touch_status[entry.entry_id]
    if status.get("protocol_version", 0) < 7 or not status.get("frontend_active"):
        raise HomeAssistantError("Eine aktive ACMP-Touchmessung ist erforderlich")
    if status.get("state") in ACTIVE_STATES:
        await confirmed_touch_command(hass, entry, runtime.cancel_touch,
                                      lambda s: s.get("state") not in ACTIVE_STATES and s.get("test_mode"))
    else:
        # Re-enter preparation before cancellation. This also resets the input
        # release gate while a finger is held; firmware then accepts parameters.
        await confirmed_touch_command(hass, entry, runtime.calibrate_touch,
                                      lambda s: s.get("state") == 9 and s.get("test_mode"))
        await confirmed_touch_command(hass, entry, runtime.cancel_touch,
                                      lambda s: s.get("state") == 8 and s.get("test_mode"))


async def apply_values(hass, entry, values):
    validate_values(values)  # Invalid input must not cancel an active session.
    runtime = entry.runtime_data
    lock = runtime.touch_parameter_locks.setdefault(entry.entry_id, asyncio.Lock())
    async with lock:
        await pause_for_adjustment(hass, entry)
        # Each tuple is committed by the existing firmware journal. Only its
        # echoed values count as success; leave actions blocked on partial error.
        profile_written = False
        try:
            await runtime._write_touch_values(entry, {k: values[k] for k in PROFILE_FIELDS}, tuning=False, quiet=True)
            profile_written = True
            await runtime._write_touch_values(entry, {k: values[k] for k in protocol.TUNING_FIELDS}, tuning=True, quiet=True)
        except COMMAND_ERRORS as err:
            detail = "Schwellen gespeichert; Filter noch nicht bestätigt. " if profile_written else "Werte noch nicht bestätigt. "
            raise HomeAssistantError(detail + "Bitte Fläche loslassen und erneut übernehmen. Schaltaktionen bleiben pausiert.") from err
