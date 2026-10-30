"""Versioned bench protocol over manufacturer-specific Basic commands.

No light server is created on the switch. Acknowledged Begin/Target/Commit
commands stage a complete configuration before replacing the active one.
"""

import hashlib
import logging
import struct
from dataclasses import dataclass

import zigpy.types as t
from zigpy.zcl import foundation

from .const import ENDPOINT, MANUFACTURER, MAX_TARGETS, RESTORE_SCENE

_LOGGER = logging.getLogger(__name__)


class Begin(t.Struct):
    revision: t.uint32_t
    group: t.uint16_t
    scene: t.uint8_t
    count: t.uint8_t


class TargetCommand(t.Struct):
    index: t.uint8_t
    ieee: t.EUI64
    node: t.uint16_t
    endpoint: t.uint8_t


class Commit(t.Struct):
    revision: t.uint32_t


class TouchParameters(t.Struct):
    baseline: t.uint32_t
    press_delta: t.uint32_t
    release_delta: t.uint32_t


def valid_touch_parameters(baseline, press_delta, release_delta):
    return (
        all(isinstance(value, int) and not isinstance(value, bool)
            for value in (baseline, press_delta, release_delta))
        and 1 <= baseline <= 1000
        and 1 <= release_delta < press_delta < 2000 - baseline
    )


async def set_touch_parameters(device, baseline, press_delta, release_delta):
    if not valid_touch_parameters(baseline, press_delta, release_delta):
        raise ValueError("Ungültige Touchparameter: Loslassabstand < Einschaltabstand erforderlich")
    await request(device, 0x47, TouchParameters, baseline, press_delta, release_delta)


TUNING_FIELDS = ("press_debounce_ms", "release_debounce_ms", "filter_samples", "gap_grace_ms", "release_stable_ms")
TUNING_BOUNDS = ((1, 100), (1, 200), (1, 7), (0, 100), (20, 500))


class TouchTuning(t.Struct):
    press_ms: t.uint8_t
    release_ms: t.uint8_t
    filter_samples: t.uint8_t
    gap_ms: t.uint8_t
    stable_ms: t.uint16_t
    reserved: t.uint16_t


def valid_touch_tuning(*values):
    return (len(values) == 5 and all(
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
        for value, (low, high) in zip(values, TUNING_BOUNDS, strict=True)
    ) and values[2] % 2 == 1)


async def set_touch_tuning(device, *values):
    if not valid_touch_tuning(*values):
        raise ValueError("Ungültige Touchfilter-/Zeitparameter")
    await request(device, 0x4B, TouchTuning, *values, 0)


async def diagnose_touch(device):
    await request(device, 0x48, Empty)


async def normal_touch(device):
    await request(device, 0x49, Empty)


async def baseline_touch(device):
    await request(device, 0x4A, Empty)


class Empty(t.Struct):
    pass


@dataclass(frozen=True, order=True)
class Target:
    ieee: str
    endpoint: int
    node: int


def revision(group: int, targets: list[Target]) -> int:
    """Stable across HA restarts and shared by switches in the same room."""
    content = struct.pack("<HB", group, RESTORE_SCENE)
    for target in sorted(set(targets)):
        content += t.EUI64.convert(target.ieee).serialize()
        content += struct.pack("<HB", target.node, target.endpoint)
    return int.from_bytes(hashlib.sha256(content).digest()[:4], "little")


async def request(device, command: int, schema, *args):
    response = (
        await device.endpoints[ENDPOINT]
        .in_clusters[0]
        .request(
            False,
            command,
            schema,
            *args,
            manufacturer=MANUFACTURER,
            expect_reply=True,
            ask_for_ack=True,
            disable_default_response=False,
        )
    )
    status = getattr(response, "status", None)
    if status is None and isinstance(response, (list, tuple)) and len(response) >= 2:
        status = response[1]
    if status != foundation.Status.SUCCESS:
        _LOGGER.warning("MoSwitch command 0x%02x: unexpected acknowledgement %r", command, response)
        raise RuntimeError(f"MoSwitch command 0x{command:02x} rejected: {response!r}")


async def configure(device, group: int, targets: list[Target]) -> int:
    targets = sorted(set(targets))
    if not 1 <= len(targets) <= MAX_TARGETS or not 1 <= group < 0xFFF8:
        raise ValueError("Unsupported room size or group id")
    value = revision(group, targets)
    await request(device, 0x40, Begin, value, group, RESTORE_SCENE, len(targets))
    for index, target in enumerate(targets):
        await request(
            device,
            0x41,
            TargetCommand,
            index,
            t.EUI64.convert(target.ieee),
            target.node,
            target.endpoint,
        )
    await request(device, 0x42, Commit, value)
    return value


async def clear(device):
    await request(device, 0x43, Empty)


async def calibrate_touch(device):
    await request(device, 0x44, Empty)


async def query_touch(device):
    await request(device, 0x45, Empty)


async def cancel_touch(device):
    await request(device, 0x46, Empty)


def touch_status(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) not in (20, 24, 56, 68, 76, 84):
        return None
    version, state, flags, progress, raw, baseline, press, release = struct.unpack(
        "<BBBBIIII", payload[:20]
    )
    if flags & ~(63 if version >= 8 else 31 if version >= 5 else 3) or (flags & 8 and not flags & 4):
        return None
    if version == 1:
        if len(payload) != 20 or state > 6 or progress:
            return None
    elif version in (2, 3, 4, 5, 6, 7, 8, 9, 10):
        if len(payload) != (84 if version == 9 else 76 if version >= 7 else 24 if version == 2 else 68 if version >= 5 else 56) or state > (13 if version >= 7 else 10 if version >= 5 else 8) or progress > 10:
            return None
    else:
        return None
    if version == 10 and (payload[26] not in (1, 2) or payload[27] & 15 != 1 or payload[27] >> 4 > 5):
        return None
    if version >= 3 and (payload[24] > (11 if version >= 7 else 8) or payload[25] > (7 if version >= 7 else 6) or (False if version == 10 else payload[26:28] not in (b"\1\1", b"\2\1") if version >= 7 else payload[26:28] != b"\0\0")):
        return None
    data = {
        "state": state,
        "calibrated": bool(flags & 1),
        "pressed": bool(flags & 2),
        "raw_us": None if raw == 0xFFFFFFFF else raw,
        "baseline_us": baseline,
        "press_delta_us": press,
        "release_delta_us": release,
        "learning_taps": progress,
        "required_taps": 10,
        "weakest_tap_delta_us": struct.unpack("<I", payload[20:24])[0] if version >= 2 else None,
        "protocol_version": version,
        "parameters_writable": version >= 4,
    }
    if version >= 3:
        data.update(zip(
            ("detected_contacts", "rejected_contacts", "last_duration_ms", "last_delta_us",
             "invalid_samples", "learning_guard_us", "learning_baseline_us"),
            struct.unpack("<IIIIIII", payload[28:56]), strict=True,
        ))
        data["feedback"] = payload[24]
        data["failure_reason"] = payload[25]
    if version >= 5:
        filtered, press_ms, release_ms, samples, gap_ms, stable_ms, reserved = struct.unpack("<IBBBBHH", payload[56:68])
        values = (press_ms, release_ms, samples, gap_ms, stable_ms)
        clock_invalid = (reserved != 0 if version == 5 else (
            reserved > 90 or (reserved != 0 and (state != 7 or not data["detected_contacts"]))
        ))
        if clock_invalid or not valid_touch_tuning(*values) or (version < 7 and gap_ms > 30):
            return None
        data.update(zip(TUNING_FIELDS, values, strict=True))
        data.update(test_mode=bool(flags & 4), training_session=bool(flags & 8),
                    measurement_valid=bool(flags & 16),
                    filtered_us=None if filtered == 0xFFFFFFFF else filtered,
                    tuning_writable=True)
    if version >= 6:
        data["learning_remaining_s"] = reserved
    if version >= 7:
        noise, hold_ms, faults = struct.unpack("<IHH", payload[68:76])
        if hold_ms > 2000:
            return None
        if state in (11, 12, 13) and (progress != 10 or (flags & 12) != 12):
            return None
        data.update(measurement_backend="acmp", profile_version=1, idle_noise_us=None if noise == 0xFFFFFFFF else noise,
                    hold_remaining_ms=hold_ms, capture_faults=faults, comparator_reference=32,
                    comparator_hysteresis=3, frontend_active=payload[26] == 1, hold_verification_required=True)
    if version >= 8:
        if flags & 32 and not flags & 4:
            return None
        data["actions_locked"] = bool(flags & 32)
    if version == 10:
        data.update(startup_check=payload[27] >> 4, startup_samples=None,
                    startup_observed_us=data.get("filtered_us") or data.get("raw_us"))
    if version == 9:
        check, reserved, samples, observed = struct.unpack("<BBHI", payload[76:84])
        if check > 5 or reserved or observed >= 2000:
            return None
        data.update(startup_check=check, startup_samples=samples, startup_observed_us=observed)
    return data
