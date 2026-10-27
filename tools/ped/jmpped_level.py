#!/usr/bin/env python3
"""jmpped_source.py - read Driver 2's Tanner and Jericho ped models from a level.

The source of truth is the level's model table, not a previously baked
JMPPED.LEV. Havana carries the six JERI_* models and the full Tanner set in the
same LUMP_MODELS; Tanner's parts all paint from texture set 2, Jericho's torso and
arms from set 23, and Jericho's head from set 25.

JMPPED v3 only has room for the six Jericho upper-body parts plus Tanner's eight
limbs (slots 6..13). The other Tanner parts are still exported so the whole
character can be inspected and edited; `bake_slot` marks which parts the archive
actually consumes.
"""

from __future__ import annotations

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract_level_peds as bj
import jmpped_core as jc
import jmpped_models as jm

TANNER_NAMES = [
    "TORSO", "HEAD", "U_ARM_RIGHT", "L_ARM_RIGHT", "HAND_RIGHT",
    "THIGH_RIGHT", "CALF_RIGHT", "FOOT_LEFT", "U_ARM_LEFT",
    "L_ARM_LEFT", "HAND_LEFT", "THIGH_LEFT", "CALF_LEFT",
    "FOOT_RIGHT", "NECK", "HIPS", "BAG",
]
JERICHO_NAMES = list(bj.MODEL_NAMES[:6])

# The v3 JMPPED slot map: six Jericho parts, then Tanner's eight limbs.
JERICHO_BAKE_SLOTS = {name: i for i, name in enumerate(JERICHO_NAMES)}
TANNER_BAKE_SLOTS = {
    "THIGH_LEFT": 6, "CALF_LEFT": 7, "FOOT_LEFT": 8,
    "THIGH_RIGHT": 9, "CALF_RIGHT": 10, "FOOT_RIGHT": 11,
    "HAND_LEFT": 12, "HAND_RIGHT": 13,
}

# The parts used for the assembled Tanner preview, in the preview's slot order.
TANNER_ASSEMBLY_ORDER = [
    "TORSO", "HEAD", "U_ARM_LEFT", "L_ARM_LEFT", "U_ARM_RIGHT",
    "L_ARM_RIGHT", "THIGH_LEFT", "CALF_LEFT", "FOOT_LEFT",
    "THIGH_RIGHT", "CALF_RIGHT", "FOOT_RIGHT", "HAND_LEFT", "HAND_RIGHT",
]

PED_PAGES = {
    "tanner": {2},
    "jericho": {23, 25},
}

# The engine's LIMBS enum and the parent links from motion_c.c's Skel table.
BONE_NAMES = [
    "ROOT", "LOWERBACK", "JOINT_1", "NECK", "HEAD",
    "LSHOULDER", "LELBOW", "LHAND", "LFINGERS",
    "RSHOULDER", "RELBOW", "RHAND", "RFINGERS",
    "HIPS", "LHIP", "LKNEE", "LFOOT", "LTOE",
    "RHIP", "RKNEE", "RFOOT", "RTOE", "JOINT",
]
BONE_PARENTS = [
    -1, 0, 1, 2, 3,
    2, 5, 6, 7,
    2, 9, 10, 11,
    0, 13, 14, 15, 16,
    13, 18, 19, 20,
    1,
]

# Actual model bindings from Skel[] / SetSkelModelPointers. LTOE/RTOE really do
# bind the opposite foot names in pmTannerModels (13 and 7); that is what the
# engine does and exactly what the skeleton export should show.
TANNER_BINDINGS = {
    "JOINT_1": "TORSO",
    "NECK": "NECK",
    "HEAD": "HEAD",
    "LELBOW": "U_ARM_LEFT",
    "LHAND": "L_ARM_LEFT",
    "LFINGERS": "HAND_LEFT",
    "RELBOW": "U_ARM_RIGHT",
    "RHAND": "L_ARM_RIGHT",
    "RFINGERS": "HAND_RIGHT",
    "LHIP": "HIPS",
    "LKNEE": "THIGH_LEFT",
    "LFOOT": "CALF_LEFT",
    "LTOE": "FOOT_RIGHT",
    "RKNEE": "THIGH_RIGHT",
    "RFOOT": "CALF_RIGHT",
    "RTOE": "FOOT_LEFT",
}
JERICHO_BINDINGS = {
    "JOINT_1": "JERI_TORSO",
    "HEAD": "JERI_HEAD",
    "LELBOW": "JERI_U_ARM_LEFT",
    "LHAND": "JERI_L_ARM_LEFT",
    "RELBOW": "JERI_U_ARM_RIGHT",
    "RHAND": "JERI_L_ARM_RIGHT",
    "LHIP": "HIPS",
    "LKNEE": "THIGH_LEFT",
    "LFOOT": "CALF_LEFT",
    "LTOE": "FOOT_LEFT",
    "RKNEE": "THIGH_RIGHT",
    "RFOOT": "CALF_RIGHT",
    "RTOE": "FOOT_RIGHT",
    "LFINGERS": "HAND_LEFT",
    "RFINGERS": "HAND_RIGHT",
}


class Level:
    def __init__(self, path: str, data: bytes, data1: bytes, data2: bytes,
                 names: list[str], models: bytes):
        self.path = path
        self.data = data
        self.data1 = data1
        self.data2 = data2
        self.names = names
        self.models = models


def read_level(path: str) -> Level:
    data = open(path, "rb").read()
    if len(data) < 16:
        raise jc.JMPPEDError("%s is too small to be a Driver 2 level" % path)
    table = struct.unpack_from("<8I", data, 8)
    data1 = data[table[0]:table[0] + table[1]]
    data2 = data[table[4]:table[4] + table[5]]
    names_lump = bj.find_lump(data1[8:], 12)
    if names_lump is None:
        raise jc.JMPPEDError("%s has no LUMP_MODELNAMES" % path)
    names = [n.decode("latin1") for n in names_lump.split(b"\0")]
    models = bj.find_lump(data2[8:], 1)
    if models is None:
        raise jc.JMPPEDError("%s has no LUMP_MODELS" % path)
    return Level(path, data, data1, data2, names, models)


def model_blob(level: Level, name: str) -> bytes | None:
    try:
        index = level.names.index(name)
    except ValueError:
        return None
    return bj.find_model_blob(level.models, index)


def ped_parts(level: Level, ped: str) -> list[dict]:
    if ped == "tanner":
        names, slots = TANNER_NAMES, TANNER_BAKE_SLOTS
    elif ped == "jericho":
        names, slots = JERICHO_NAMES, JERICHO_BAKE_SLOTS
    else:
        raise jc.JMPPEDError("unknown ped %r" % ped)

    out = []
    for name in names:
        blob = model_blob(level, name)
        if blob is None:
            # BAG is absent from Havana's table; the rest are required.
            if name == "BAG":
                continue
            raise jc.JMPPEDError("%s: model %s is missing from %s"
                                 % (ped, name, level.path))
        model = jm.parse_model(blob)
        page_sets = {p.set for p in model.polys if p.textured}
        out.append({
            "name": name,
            "blob": blob,
            "model": model,
            "page_sets": sorted(page_sets),
            "bake_slot": slots.get(name),
        })
    return out


def check_level_pages(level: Level, parts: list[dict], ped: str) -> None:
    allowed = PED_PAGES[ped]
    for part in parts:
        extra = set(part["page_sets"]) - allowed
        if extra:
            raise jc.JMPPEDError(
                "%s/%s uses texture page(s) %s, but the %s source export only "
                "carries %s" % (ped, part["name"], sorted(extra), ped,
                                sorted(allowed)))


def motion_block(level: Level) -> bytes | None:
    """The first LUMP_MOTIONCAPTURE payload, the shared ped bind pose.

    The first 146 bytes hold the bone positions (bones 1..22 at +14, root at
    +146); frame 0's root/rotations follow. We only need the bind positions.
    """
    for body in (level.data1[8:], level.data2[8:]):
        lump = bj.find_lump(body, 22)
        if lump is not None and len(lump) >= 152:
            return lump
    return None


def skeleton_bones(level: Level) -> dict | None:
    """Raw bind positions for the 23 LIMBS bones.

    Engine raw coordinates: +Y is DOWN. The draw path negates Y; the exporter's
    Blender copy applies (x, z, -y), so the skeleton and the raw meshes share one
    space and can be compared directly.
    """
    motion = motion_block(level)
    if motion is None:
        return None
    raw = [struct.unpack_from("<hhh", motion, 146)]
    for i in range(1, len(BONE_NAMES)):
        offset = 14 + (i - 1) * 6
        if offset + 6 > len(motion):
            return None
        raw.append(struct.unpack_from("<hhh", motion, offset))
    # The motion block is Y-up; the engine draw path negates Y and Z when it
    # builds Skel[].vCurrPos, and the mesh/engine space is +Y down. Export both
    # so the skeleton can be compared with either view.
    draw = [(x, -y, -z) for x, y, z in raw]
    return {"motion_size": len(motion), "raw": raw, "draw": draw}
