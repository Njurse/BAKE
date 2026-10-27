#!/usr/bin/env python3
"""jmpped_core.py - constants, errors, JSON and hash helpers."""

from __future__ import annotations

import hashlib
import json
import os


MAGIC = b"JMPPED\x00\x00"
VERSION_V3 = 3
VERSION_V4 = 4
VERSION_V5 = 5

# v5 layout:
#   MAGIC                                     8
#   u32 version = 5                           4
#   u32 entry_count                           4
#   per entry:
#     u32 name_len, name bytes
#     u32 model_count (14)
#     model_count * (u32 size, MODEL blob)
#     u32 page_count
#     page_count * (u32 set, u32 npalettes, u32 clut_len, u32 image_len,
#                   u32 flags, clut, image)
# Entry 0 is the existing Jericho set (sets 23/25/2). Custom entries use their
# own page sets so pages and CLUTs cannot collide. The v3 reader stays for
# existing archives and the Havana base bake.
JMPPED_MAX_ENTRIES = 8
ENTRY_NAME_MAX = 63

# Fixed page-set allocation per entry. Entry 0 is the existing Jericho page set;
# every later entry owns three fresh sets, so their pages/CLUTs can never alias.
# The three values are (torso+arms, head, legs/hands).
ENTRY_PAGE_SETS = (
    (23, 25, 2),
    (26, 27, 3),
    (28, 29, 4),
    (30, 31, 5),
    (32, 33, 6),
    (34, 35, 7),
    (36, 37, 8),
    (38, 39, 9),
)


def entry_page_sets(index: int) -> tuple[int, int, int]:
    if index < 0 or index >= len(ENTRY_PAGE_SETS):
        raise JMPPEDError("entry %d has no page-set allocation" % index)
    return ENTRY_PAGE_SETS[index]


MODEL_HEADER_SIZE = 36
MODEL_HEADER_FMT = "<HHhBBhHHHiiiii"
PAGE_W = 256
PAGE_H = 256
PAGE_INDEX_BYTES = PAGE_W * PAGE_H // 2       # 4bpp page, two indices per byte
PAGE_BYTES = PAGE_W * PAGE_H                   # decoded index count
CLUT_ROW_BYTES = 32
CLUT_ROW_ENTRIES = 16
CLUT_GROUP_BYTES = 128                        # four CLUT rows
PAGE_FLAG_RAW = 0
PAGE_FLAG_COMPRESSED = 1
SECTOR = 2048

MODEL_NAMES = [
    "JERI_TORSO", "JERI_HEAD",
    "JERI_U_ARM_LEFT", "JERI_L_ARM_LEFT",
    "JERI_U_ARM_RIGHT", "JERI_L_ARM_RIGHT",
    "THIGH_LEFT", "CALF_LEFT", "FOOT_LEFT",
    "THIGH_RIGHT", "CALF_RIGHT", "FOOT_RIGHT",
    "HAND_LEFT", "HAND_RIGHT",
]
PED_HEAD_MODEL = "PEDHEAD1"
V4_MODEL_NAMES = MODEL_NAMES + [PED_HEAD_MODEL]
PED_TEXTURE_NAMES = ["CHEST1", "ARM1", "FOREARM1", "JEANS1", "LOWHEAD", "HEAD1"]
POLY_SIZES = [8, 12, 16, 24, 20, 20, 28, 32, 8, 12, 16, 16,
              0, 0, 0, 0, 12, 12, 12, 16, 20, 20, 24, 24,
              0, 0, 0, 0, 0, 0, 0, 0, 8, 12, 16, 24,
              20, 24, 28, 32, 0, 0, 0, 0, 0, 0, 0, 0,
              12, 12, 12, 16, 20, 20, 24, 24]
POLY_FT3 = 20
POLY_FT4 = 21
POLY_GT3 = 22
POLY_GT4 = 23
TEXTURED_KINDS = {4, 5, 6, 7, 20, 21, 22, 23}
POLY_NAMES = {POLY_FT3: "FT3", POLY_FT4: "FT4", POLY_GT3: "GT3", POLY_GT4: "GT4"}

# Blender's Z-up view. The raw OBJ is authoritative; this transform only exists
# for the editing copy.
BLENDER_SCALE = 1.0 / 1024.0


class JMPPEDError(Exception):
    """A malformed archive or an edit this toolkit cannot represent."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

# --------------------------------------------------------------------------
# Manifest helpers
# --------------------------------------------------------------------------

MANIFEST_VERSION = 1


def sha256_bytes_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
