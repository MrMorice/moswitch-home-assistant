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
