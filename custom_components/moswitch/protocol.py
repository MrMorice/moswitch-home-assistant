"""Versioned bench protocol over manufacturer-specific Basic commands.

No light server is created on the switch. Acknowledged Begin/Target/Commit
commands stage a complete configuration before replacing the active one.
"""

import hashlib
import struct
from dataclasses import dataclass

import zigpy.types as t
from zigpy.zcl import foundation

from .const import ENDPOINT, MANUFACTURER, MAX_TARGETS, RESTORE_SCENE


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
            disable_default_response=False,
        )
    )
    status = getattr(response, "status", None)
    if status is None and isinstance(response, (list, tuple)) and len(response) >= 2:
        status = response[1]
    if status != foundation.Status.SUCCESS:
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
    if not isinstance(payload, (bytes, bytearray)) or len(payload) not in (20, 24):
        return None
    version, state, flags, progress, raw, baseline, press, release = struct.unpack(
        "<BBBBIIII", payload[:20]
    )
    if flags & ~3:
        return None
    if version == 1:
        if len(payload) != 20 or state > 6 or progress:
            return None
    elif version == 2:
        if len(payload) != 24 or state > 8 or progress > 10:
            return None
    else:
        return None
    return {
        "state": state,
        "calibrated": bool(flags & 1),
        "pressed": bool(flags & 2),
        "raw_us": None if raw == 0xFFFFFFFF else raw,
        "baseline_us": baseline,
        "press_delta_us": press,
        "release_delta_us": release,
        "learning_taps": progress,
        "required_taps": 10,
        "weakest_tap_delta_us": struct.unpack("<I", payload[20:])[0] if version == 2 else None,
        "protocol_version": version,
    }
