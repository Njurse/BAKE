#!/usr/bin/env python3
"""jmpped_archive.py - JMPPED v3/v4/v5 archive read/write."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from jmpped_core import *
from jmpped_models import Model, parse_model


def _u32(data: bytes, off: int) -> int:
    if off + 4 > len(data):
        raise JMPPEDError("truncated archive at offset %d" % off)
    return struct.unpack_from("<I", data, off)[0]


@dataclass
class Page:
    set: int
    npalettes: int
    clut: bytes
    image: bytes
    flags: int
    raw_blob: bytes = b""

    @property
    def compressed(self) -> bool:
        return bool(self.flags & 1)


@dataclass
class PedTexture:
    texnum: int
    x: int
    y: int
    w: int
    h: int


@dataclass
class PedPalette:
    texnum: int
    slot: int
    row: bytes


@dataclass
class Entry:
    name: str
    models: list[Model]
    pages: list[Page]
    raw_models: list[bytes] = field(default_factory=list)
    raw_pages: list[bytes] = field(default_factory=list)


@dataclass
class Archive:
    version: int
    model_names: list[str]
    models: list[Model]
    pages: list[Page]
    ped_model_index: int | None = None
    ped_pages: list[Page] = field(default_factory=list)
    ped_textures: list[PedTexture] = field(default_factory=list)
    ped_palettes: list[PedPalette] = field(default_factory=list)
    source_hash: str = ""
    raw_models: list[bytes] = field(default_factory=list)
    raw_pages: list[bytes] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    @property
    def is_v4(self) -> bool:
        return self.version == VERSION_V4

    @property
    def is_v5(self) -> bool:
        return self.version == VERSION_V5


def read_archive(path: str) -> Archive:
    data = open(path, "rb").read()
    return parse_archive(data)


def parse_archive(data: bytes) -> Archive:
    if len(data) < 16 or data[:8] != MAGIC:
        raise JMPPEDError("not a JMPPED archive")
    version, count = struct.unpack_from("<II", data, 8)
    if version == VERSION_V5:
        return _parse_v5(data)
    if version not in (VERSION_V3, VERSION_V4):
        raise JMPPEDError("unsupported JMPPED version %d" % version)
    expected = 14 if version == VERSION_V3 else 15
    names = MODEL_NAMES if version == VERSION_V3 else V4_MODEL_NAMES
    if count != expected:
        raise JMPPEDError("version %d expects %d models, archive has %d"
                         % (version, expected, count))

    off = 16
    raw_models: list[bytes] = []
    models: list[Model] = []
    for _ in range(count):
        size = _u32(data, off)
        off += 4
        if off + size > len(data):
            raise JMPPEDError("truncated model blob at offset %d" % off)
        blob = data[off:off + size]
        off += size
        raw_models.append(blob)
        models.append(parse_model(blob))

    page_count = _u32(data, off)
    off += 4
    pages: list[Page] = []
    raw_pages: list[bytes] = []
    for _ in range(page_count):
        start = off
        setid, npal, clut_len, image_len, flags = struct.unpack_from("<IIIII", data, off)
        off += 20
        if off + clut_len + image_len > len(data):
            raise JMPPEDError("truncated page record at offset %d" % off)
        clut = data[off:off + clut_len]
        off += clut_len
        image = data[off:off + image_len]
        off += image_len
        pages.append(Page(setid, npal, clut, image, flags,
                          raw_blob=data[start:off]))
        raw_pages.append(data[start:off])

    arch = Archive(version=version, model_names=names, models=models,
                   pages=pages, raw_models=raw_models, raw_pages=raw_pages,
                   source_hash=sha256_bytes(data))
    arch.entries = [Entry("Jericho", models, pages, raw_models, raw_pages)]

    if version == VERSION_V4:
        arch.ped_model_index = _u32(data, off)
        off += 4
        ped_page_count = _u32(data, off)
        off += 4
        for _ in range(ped_page_count):
            setid, npal, clut_len, image_len, flags = struct.unpack_from("<IIIII", data, off)
            off += 20
            if off + clut_len + image_len > len(data):
                raise JMPPEDError("truncated ped page record at offset %d" % off)
            clut = data[off:off + clut_len]
            off += clut_len
            image = data[off:off + image_len]
            off += image_len
            arch.ped_pages.append(Page(setid, npal, clut, image, flags))
        tex_count = _u32(data, off)
        off += 4
        for _ in range(tex_count):
            texnum, x, y, w, h = struct.unpack_from("<IIIII", data, off)
            off += 20
            arch.ped_textures.append(PedTexture(texnum, x, y, w, h))
        pal_count = _u32(data, off)
        off += 4
        for _ in range(pal_count):
            texnum, slot = struct.unpack_from("<II", data, off)
            off += 8
            row = data[off:off + CLUT_ROW_BYTES]
            if len(row) != CLUT_ROW_BYTES:
                raise JMPPEDError("truncated ped palette row at offset %d" % off)
            off += CLUT_ROW_BYTES
            arch.ped_palettes.append(PedPalette(texnum, slot, row))

    if off != len(data):
        raise JMPPEDError("%d trailing byte(s) after the archive" % (len(data) - off))
    return arch


def _parse_v5(data: bytes) -> Archive:
    version, entry_count = struct.unpack_from("<II", data, 8)

    if version != VERSION_V5:
        raise JMPPEDError("not a v5 archive")
    if entry_count < 1 or entry_count > JMPPED_MAX_ENTRIES:
        raise JMPPEDError("v5 archive has %d entries (max %d)"
                          % (entry_count, JMPPED_MAX_ENTRIES))

    off = 16
    entries: list[Entry] = []
    seen_sets: dict[int, str] = {}

    for _ in range(entry_count):
        if off + 4 > len(data):
            raise JMPPEDError("truncated v5 entry header")
        name_len = _u32(data, off)
        off += 4
        if name_len == 0 or name_len > ENTRY_NAME_MAX or off + name_len > len(data):
            raise JMPPEDError("bad v5 entry name length %d" % name_len)
        name = data[off:off + name_len].decode("utf-8")
        off += name_len
        if any(e.name == name for e in entries):
            raise JMPPEDError("duplicate v5 entry name %r" % name)

        model_count = _u32(data, off)
        off += 4
        if model_count != len(MODEL_NAMES):
            raise JMPPEDError("entry %r has %d models; expected %d"
                              % (name, model_count, len(MODEL_NAMES)))

        raw_models: list[bytes] = []
        models: list[Model] = []
        for _slot in range(model_count):
            if off + 4 > len(data):
                raise JMPPEDError("truncated model size in entry %r" % name)
            size = _u32(data, off)
            off += 4
            if size < MODEL_HEADER_SIZE or off + size > len(data):
                raise JMPPEDError("entry %r has a short model blob" % name)
            blob = data[off:off + size]
            off += size
            raw_models.append(blob)
            models.append(parse_model(blob))

        page_count = _u32(data, off)
        off += 4
        if page_count < 1 or page_count > 64:
            raise JMPPEDError("entry %r has %d pages" % (name, page_count))

        pages: list[Page] = []
        raw_pages: list[bytes] = []
        for _page in range(page_count):
            start = off
            if off + 20 > len(data):
                raise JMPPEDError("truncated page header in entry %r" % name)
            setid, npal, clut_len, image_len, flags = struct.unpack_from("<IIIII", data, off)
            off += 20
            if off + clut_len + image_len > len(data):
                raise JMPPEDError("truncated page record in entry %r" % name)
            clut = data[off:off + clut_len]
            off += clut_len
            image = data[off:off + image_len]
            off += image_len
            if setid in seen_sets:
                raise JMPPEDError("page set %d is used by both %r and %r"
                                  % (setid, seen_sets[setid], name))
            seen_sets[setid] = name
            pages.append(Page(setid, npal, clut, image, flags,
                              raw_blob=data[start:off]))
            raw_pages.append(data[start:off])

        entries.append(Entry(name, models, pages, raw_models, raw_pages))

    if off != len(data):
        raise JMPPEDError("%d trailing byte(s) after the v5 archive" % (len(data) - off))

    first = entries[0]
    return Archive(version=VERSION_V5, model_names=MODEL_NAMES,
                   models=first.models, pages=first.pages,
                   raw_models=first.raw_models, raw_pages=first.raw_pages,
                   entries=entries, source_hash=sha256_bytes(data))


def archive_bytes(arch: Archive) -> bytes:
    """Serialize an archive. Untouched model/page blobs are emitted verbatim."""
    if arch.version == VERSION_V5:
        entries = arch.entries
        if not entries:
            entries = [Entry("Jericho", arch.models, arch.pages,
                             arch.raw_models, arch.raw_pages)]
        return _archive_v5_bytes(entries)

    out = bytearray(MAGIC)
    out += struct.pack("<II", arch.version, len(arch.models))
    for i, m in enumerate(arch.models):
        raw = arch.raw_models[i] if i < len(arch.raw_models) else m.blob
        out += struct.pack("<I", len(raw))
        out += raw
    out += struct.pack("<I", len(arch.pages))
    for i, p in enumerate(arch.pages):
        raw = arch.raw_pages[i] if i < len(arch.raw_pages) else None
        if raw:
            out += raw
        else:
            out += _page_record(p)
    if arch.version == VERSION_V4:
        out += struct.pack("<I", arch.ped_model_index if arch.ped_model_index is not None else 14)
        out += struct.pack("<I", len(arch.ped_pages))
        for p in arch.ped_pages:
            out += _page_record(p)
        out += struct.pack("<I", len(arch.ped_textures))
        for t in arch.ped_textures:
            out += struct.pack("<IIIII", t.texnum, t.x, t.y, t.w, t.h)
        out += struct.pack("<I", len(arch.ped_palettes))
        for p in arch.ped_palettes:
            out += struct.pack("<II", p.texnum, p.slot)
            out += p.row
    return bytes(out)


def _archive_v5_bytes(entries: list[Entry]) -> bytes:
    if len(entries) < 1 or len(entries) > JMPPED_MAX_ENTRIES:
        raise JMPPEDError("v5 archive has %d entries (max %d)"
                          % (len(entries), JMPPED_MAX_ENTRIES))

    names: set[str] = set()
    page_sets: set[int] = set()
    out = bytearray(MAGIC)
    out += struct.pack("<II", VERSION_V5, len(entries))

    for entry in entries:
        name = entry.name.encode("utf-8")
        if not name or len(name) > ENTRY_NAME_MAX:
            raise JMPPEDError("entry name %r is not 1..%d UTF-8 bytes"
                              % (entry.name, ENTRY_NAME_MAX))
        if entry.name in names:
            raise JMPPEDError("duplicate entry name %r" % entry.name)
        names.add(entry.name)

        if len(entry.models) != len(MODEL_NAMES):
            raise JMPPEDError("entry %r has %d models; expected %d"
                              % (entry.name, len(entry.models), len(MODEL_NAMES)))

        out += struct.pack("<I", len(name))
        out += name
        out += struct.pack("<I", len(entry.models))
        for i, m in enumerate(entry.models):
            raw = entry.raw_models[i] if i < len(entry.raw_models) else m.blob
            out += struct.pack("<I", len(raw))
            out += raw

        out += struct.pack("<I", len(entry.pages))
        for i, p in enumerate(entry.pages):
            if p.set in page_sets:
                raise JMPPEDError("duplicate page set %d in entry %r"
                                  % (p.set, entry.name))
            page_sets.add(p.set)
            raw = entry.raw_pages[i] if i < len(entry.raw_pages) else None
            out += raw if raw else _page_record(p)

    return bytes(out)


def _page_record(page: Page) -> bytes:
    return (struct.pack("<IIIII", page.set, page.npalettes, len(page.clut),
                        len(page.image), page.flags) + page.clut + page.image)


def write_archive(path: str, arch: Archive) -> bytes:
    data = archive_bytes(arch)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data
