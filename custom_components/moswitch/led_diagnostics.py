"""Prepared internal ring diagnostics; no light entity or automatic activation."""

import asyncio
import secrets
import struct

import probatio as vol
from homeassistant.components import websocket_api
from zigpy import types as t

from .const import ENDPOINT, MANUFACTURER
from .touch_wizard import COMMAND_ERRORS

OPS = {"disable": 0, "enable": 1, "test": 2, "confirm_supply": 3,
       "forget_supply": 4, "brightness": 5, "status": 6, "wire_check": 7,
       "boot_status": 8, "pad_check": 9, "journal_test": 10}

PROBE_STAGES = {
    0: "boot", 10: "probe_begin", 11: "transport_change_begin", 12: "transport_ready",
    20: "capture_begin", 100: "capture_resources_begin", 101: "capture_clocks_enabled",
    102: "capture_resources_checked", 103: "capture_dma_allocated", 104: "gpio_input_routed",
    105: "prs_signal_connected", 106: "prs_consumer_connected", 200: "capture_arm_begin",
    201: "before_dma_stop", 202: "before_timer_stop", 203: "before_timer_reset",
    204: "before_timer_init", 205: "before_timer_cc_init", 206: "before_capture_dma_start",
    207: "before_timer_start", 208: "capture_armed", 301: "before_selftest_gpio_high",
    303: "before_selftest_gpio_low", 310: "before_selftest_finish", 311: "finish_before_timer_stop",
    312: "before_dma_remaining_count", 313: "finish_before_dma_stop", 400: "before_frame_capture_arm",
    401: "before_spi_send", 402: "spi_send_started", 403: "spi_wait_finished", 404: "before_decode",
    500: "cleanup_begin", 501: "cleanup_before_dma_stop", 503: "cleanup_before_timer_stop",
    504: "cleanup_before_timer_reset", 505: "cleanup_restore_routes", 506: "cleanup_restore_clocks",
    600: "pad_check_begin", 601: "pad_route_detached", 602: "pad_low_before_read",
    603: "before_pad_high", 604: "pad_high_read", 606: "pad_low_after_read",
    900: "probe_completed", 901: "probe_refused_or_cancelled", 950: "controlled_reboot_armed",
}


class RingCommand(t.Struct):
    operation: t.uint8_t
    value: t.uint8_t
    nonce: t.uint16_t


def parse_ring(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) not in (16, 56):
        return None
    version, flags, brightness, error, baud, frames, nonce, operation, result = struct.unpack("<BBBBIIHBB", payload[:16])
    if not ((version == 1 and len(payload) == 16 and operation <= 6)
            or (version in (2, 3) and len(payload) == 56 and operation == 7)
            or (version == 4 and len(payload) == 56 and operation in (8, 9, 10))):
        return None
    if flags > 15 or brightness > 64 or result > 1:
        return None
    status = {"supply_confirmed": bool(flags & 1), "enabled": bool(flags & 2),
            "started": bool(flags & 4), "test_active": bool(flags & 8),
            "brightness": brightness, "error": error, "baud": baud, "frames": frames,
            "nonce": nonce, "operation": operation, "result": result}
    if version == 2:
        executed, levels, mode, route_enable, reference_hz, samples, high, transitions, max_gap, duration, first_high, route, core_hz = struct.unpack("<BBBB9I", payload[16:])
        if (executed > 1 or levels > 7 or mode > 15 or high > samples
                or transitions > samples or (executed and not core_hz)):
            return None
        us = 1_000_000 / core_hz if core_hz else 0
        coverage = bool(executed and levels & 4 and samples >= 64
                        and duration * us >= 700 and max_gap * us < 100)
        activity = bool(high and high < samples and transitions >= 2)
        status["wire_probe"] = {
            "executed": bool(executed), "input_before": bool(levels & 1),
            "input_after": bool(levels & 2), "transfer_complete": bool(levels & 4),
            "gpio_mode": mode, "route_enable": route_enable, "tx_route": route,
            "routing_matches_expected": mode == 4 and route_enable == 16 and route == 0x20002,
            "reference_hz": reference_hz, "core_hz": core_hz, "samples": samples,
            "high_samples": high, "transitions_observed": transitions,
            "max_sample_gap_us": round(max_gap * us, 3),
            "duration_us": round(duration * us, 3),
            "first_high_us": round(first_high * us, 3) if first_high != 0xFFFFFFFF else None,
            "coverage_sufficient": coverage,
            "output_activity_observed": True if activity else False if coverage else None,
        }
    if version == 3:
        executed, levels, mode, route_enable, reference_hz, edges, rises, min_high, max_high, min_low, max_low, faults, timer_hz = struct.unpack("<BBBB9I", payload[16:])
        if (executed > 3 or mode > 15 or edges > 512 or rises != (edges + 1) // 2
                or min_high > max_high or min_low > max_low or (executed and not faults and not timer_hz)):
            return None
        us = 1_000_000 / timer_hz if timer_hz else 0
        coverage = bool(executed and levels & 4 and levels & 32 and levels & 8
                        and levels & 16 and not faults and timer_hz)
        status["wire_probe"] = {
            "executed": bool(executed), "hardware_capture": True,
            "transport_variant": executed - 1 if executed else None,
            "input_before": bool(levels & 1), "input_after": bool(levels & 2),
            "transfer_complete": bool(levels & 4), "direct_gpio_high_ok": bool(levels & 8),
            "direct_gpio_low_ok": bool(levels & 16), "capture_self_test_passed": bool(levels & 32),
            "payload_matches_expected": bool(levels & 64), "pulse_timing_within_profile": bool(levels & 128),
            "gpio_mode": mode, "route_enable": route_enable,
            "routing_matches_expected": mode == 4 and route_enable == 16 and not faults & 2048,
            "reference_hz": reference_hz, "timer_hz": timer_hz,
            "edge_count": edges, "rising_edges": rises, "capture_faults": faults,
            "resource_blockers": [name for bit, name in (
                (1, "timer2_active"), (2, "timer2_interrupts"), (4, "timer2_pin_route"),
                (8, "timer2_prs_consumer"), (16, "prs11"), (32, "gpio_interrupt2"),
                (64, "dma_channel"), (128, "timer2_clock")) if faults >> 16 & bit],
            "min_high_us": round(min_high * us, 3) if min_high else None,
            "max_high_us": round(max_high * us, 3) if max_high else None,
            "min_low_us": round(min_low * us, 3) if min_low else None,
            "max_low_us": round(max_low * us, 3) if max_low else None,
            "coverage_sufficient": coverage,
            "output_activity_observed": True if edges >= 2 else False if coverage else None,
        }
    if version == 4:
        cause, boots, previous, variant, previous_boot, current, uptime, pad, stack, journal_flags = struct.unpack("<10I", payload[16:])
        if journal_flags > 1:
            return None
        completed = bool(pad & 8)
        status["boot_diagnostics"] = {
            "reset_cause": cause,
            "reset_causes": [name for bit, name in (
                (1, "power_on"), (2, "reset_pin"), (4, "em4_wakeup"), (8, "watchdog0"),
                (16, "watchdog1"), (32, "core_lockup"), (64, "software_system_reset"),
                (128, "dvdd_brownout"), (256, "dvdd_low_energy_brownout"),
                (512, "decouple_brownout"), (1024, "avdd_brownout")) if cause & bit],
            "unknown_reset_bits": cause & ~2047,
            "boot_count_ram": boots, "retained_journal_valid": bool(journal_flags & 1),
            "previous_stage": previous, "previous_stage_name": PROBE_STAGES.get(previous, "unknown"),
            "previous_variant": variant, "previous_boot": previous_boot,
            "current_stage": current, "current_stage_name": PROBE_STAGES.get(current, "unknown"),
            "uptime_ms": uptime, "stack_high_water_words": stack,
            "pad_check_completed": completed,
            "pad_low_before_ok": bool(pad & 1) if completed else None,
            "pad_high_ok": bool(pad & 2) if completed else None,
            "pad_low_after_ok": bool(pad & 4) if completed else None,
            "pad_gpio_mode": (pad >> 8) & 15,
            "pad_route_before": (pad >> 16) & 255, "pad_route_after": (pad >> 24) & 255,
        }
    return status


def ring_reply(runtime, ieee, payload):
    status = parse_ring(payload)
    if status is None:
        return
    waiter = runtime.ring_waiters.get(ieee)
    if waiter and waiter[0] == status["nonce"] and waiter[1] == status["operation"] and not waiter[2].done():
        waiter[2].set_result(status)


async def ring_action(entry, action, value=None):
    """Supply confirmation is an operator declaration, never a rail measurement."""
    if action not in OPS or (action == "brightness" and (type(value) is not int or not 0 <= value <= 64)):
        raise ValueError("Ungültige LED-Diagnoseeinstellung")
    if action == "wire_check" and value is not None and (type(value) is not int or not 0 <= value <= 2):
        raise ValueError("Ungültige LED-Prüfvariante")
    runtime = entry.runtime_data
    device = runtime.listen_switch(entry)
    ieee = str(device.ieee)
    lock = runtime.ring_locks.setdefault(ieee, asyncio.Lock())

    async def command(operation, argument=0):
        nonce = secrets.randbelow(65535) + 1
        future = asyncio.get_running_loop().create_future()
        runtime.ring_waiters[ieee] = (nonce, operation, future)
        try:
            await device.endpoints[ENDPOINT].in_clusters[0].request(
                False, 0x4D, RingCommand, operation, argument, nonce,
                manufacturer=MANUFACTURER, expect_reply=False,
                ask_for_ack=True, disable_default_response=True,
            )
            status = await asyncio.wait_for(future, 10)
            if status["result"]:
                raise ValueError(f"LED-Befehl abgelehnt: Operation {operation}, Treiberfehler {status['error']}, Versorgung {status['supply_confirmed']}, aktiv {status['started']}")
            return status
        finally:
            runtime.ring_waiters.pop(ieee, None)
            if not future.done():
                future.cancel()

    async with lock:
        argument = 0xA5 if action == "confirm_supply" else value or 0 if action in ("brightness", "wire_check") else 0
        status = await command(OPS[action], argument)
        if action == "enable" and (not status["enabled"] or not status["started"] or status["error"]):
            raise ValueError("LED-Treiber wurde nicht gestartet")
        if action == "test" and (not status["test_active"] or not status["enabled"] or not status["started"] or status["error"]):
            raise ValueError("LED-Test wurde nicht gestartet")
        if action == "wire_check" and not status.get("wire_probe", {}).get("executed"):
            raise ValueError("LED-Leitungsprüfung wurde nicht ausgeführt")
        if action == "wire_check" and status["wire_probe"].get("hardware_capture") and status["wire_probe"].get("transport_variant") != argument:
            raise ValueError("LED-Prüfvariante wurde nicht bestätigt")
        if action == "boot_status" and "boot_diagnostics" not in status:
            raise ValueError("Resetdiagnose wurde nicht bestätigt")
        if action == "pad_check" and not status.get("boot_diagnostics", {}).get("pad_check_completed"):
            raise ValueError("GPIO-Prüfung wurde nicht abgeschlossen")
        if action == "journal_test" and status.get("boot_diagnostics", {}).get("current_stage") != 950:
            raise ValueError("Kontrollierter Diagnoseneustart wurde nicht bestätigt")
        if action == "confirm_supply" and not status["supply_confirmed"]:
            raise ValueError("Versorgungsbestätigung fehlt")
        if action == "brightness" and status["brightness"] != value:
            raise ValueError("LED-Helligkeit wurde nicht übernommen")
        if action == "forget_supply" and (status["supply_confirmed"] or status["started"]):
            raise ValueError("LED-Datenleitung wurde nicht deaktiviert")
        if action == "disable":
            for _ in range(3):
                if not status["enabled"] and not status["started"]:
                    break
                await asyncio.sleep(.1)
                status = await command(OPS["status"])
            if status["enabled"] or status["started"] or status["error"]:
                raise ValueError("LED-Abschaltung nicht fehlerfrei bestätigt")
        return status


@websocket_api.websocket_command({vol.Required("type"): "moswitch/led/action",
    vol.Required("entry_id"): str, vol.Required("action"): vol.In(tuple(OPS)),
    vol.Optional("value"): vol.All(int, vol.Range(min=0, max=64))})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ring(hass, connection, msg):
    from .panel import panel_entry

    try:
        entry = panel_entry(hass, msg["entry_id"])
        result = await ring_action(entry, msg["action"], msg.get("value"))
    except COMMAND_ERRORS as err:
        connection.send_error(msg["id"], "led_diagnostic_failed", str(err) or "Keine gültige LED-Gerätebestätigung; LED-Testfirmware erforderlich")
        return
    connection.send_result(msg["id"], result)
