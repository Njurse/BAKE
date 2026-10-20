#!/usr/bin/env python3
"""add_car_sounds.py - add LUMP_CAR_SOUNDS to an existing synthetic D1 LEV.

The bake that writes DRIVER/D1CARS/*.LEV also writes the car geometry, palettes
and wheels. Re-running that whole bake just to add sounds can change the car
mesh (a different bake produces a different model), so this tool patches ONLY
the sound segment into a known-good LEV. It reads bank 0 of VOICES.BLK, builds
one D2-shaped 3-sample bank per present model, appends the type-201 segment to
DATA1 and moves the page data to the new pageBase. Geometry bytes are untouched.
"""
import argparse
import os
import struct

LUMP_CAR_DATA = 35
LUMP_CAR_MODELS = 28
LUMP_CAR_SOUNDS = 201
MAX_CAR_MODELS = 13

# Measured mapping; see JERICHO/MODS/d1cars/tools/bake.py.
D1_ENGINE_SAMPLE = (1, 2, 3, 4, 5)
D1_HORN_SAMPLE = (6, 7, 8)

# The Euro-43 SOUND.SBK has its own bank order: 6 is the 24,800-byte 22050 Hz
# engine loop (the same shape as retail entry 3), 7 the 1,920-byte idle, and
# 8/9 one-shot horns. 11/12 are the beta's other long loops.
SOUND_SBK_ENGINE = (6, 11, 12)
SOUND_SBK_IDLE = (7, 10, 7)
SOUND_SBK_HORN = (8, 9, 3)


def read_d1_voices(voices_path):
    blob = open(voices_path, "rb").read()
    if len(blob) < 44:
        return None
    table = struct.unpack_from("<11i", blob, 0)
    if table[0] == 44 and table[0] < table[1] <= len(blob):
        body = blob[table[0]:table[1]]
        if len(body) < 4:
            return None
        count = struct.unpack_from("<i", body, 0)[0]
        dir_size = 4 + count * 16
        if count < 1 or dir_size > len(body):
            return None
        vag = body[dir_size:]
        entries = []
        for i in range(count):
            a, length, loop, rate = struct.unpack_from("<IIII", body, 4 + i * 16)
            if a + length > len(vag):
                return None
            entries.append((a, length, loop, rate))
        return {"entries": entries, "vag": vag, "kind": "voices"}

    # The Euro-43 beta ships a SOUND.SBK: a bare count + the same 16-byte
    # (address, length, loop, rate) directory, with the VAG data right after it.
    count = struct.unpack_from("<i", blob, 0)[0]
    if not (1 <= count <= 64):
        return None
    dir_size = 4 + count * 16
    if dir_size > len(blob):
        return None
    vag = blob[dir_size:]
    entries = []
    for i in range(count):
        a, length, loop, rate = struct.unpack_from("<IIII", blob, 4 + i * 16)
        if a + length > len(vag):
            return None
        entries.append((a, length, loop, rate))
    if not entries or entries[0][0] != 0:
        return None
    return {"entries": entries, "vag": vag, "kind": "sbk"}


def build_bank(voices, engine_index, horn_index, idle_index=None):
    payload = bytearray()
    entries = []

    def append(index):
        addr, length, loop, rate = voices["entries"][index]
        entries.append((len(payload), length, loop, rate))
        payload.extend(voices["vag"][addr:addr + length])

    append(engine_index)
    if idle_index is None:
        entries.append(entries[-1])      # retail: idle shares the one D1 loop
    else:
        append(idle_index)               # beta bank carries its own idle
    append(horn_index)

    bank = bytearray(struct.pack("<i", 3))
    for addr, length, loop, rate in entries:
        bank += struct.pack("<IIII", addr, length, loop, rate)
    bank += payload
    return bytes(bank)


def find_data1(blob):
    if len(blob) < 16:
        return None
    data1_off, data1_size = struct.unpack_from("<ii", blob, 8)
    if data1_off < 16 or data1_size <= 0 or data1_off + data1_size > len(blob):
        return None
    return data1_off, data1_size


def present_models(blob, data1_off, data1_size):
    off = data1_off + 8
    end = data1_off + data1_size
    while off + 8 <= end:
        t, s = struct.unpack_from("<ii", blob, off)
        if t == 0xFF or s < 0 or off + 8 + s > end:
            break
        if t == LUMP_CAR_MODELS:
            body = off + 8
            if s < 4 + MAX_CAR_MODELS * 12:
                return []
            out = []
            for m in range(MAX_CAR_MODELS):
                clean = struct.unpack_from("<i", blob, body + 4 + m * 12)[0]
                if clean != -1:
                    out.append(m)
            return out
        off += 8 + s
        off = (off + 3) & ~3
    return []


def build_sound_body(voices, models):
    offsets = [-1] * MAX_CAR_MODELS
    body = bytearray(struct.pack("<ii", 1, MAX_CAR_MODELS))
    body += struct.pack("<%di" % MAX_CAR_MODELS, *offsets)
    rows = []
    if voices.get("kind") == "sbk":
        engines, idles, horns = SOUND_SBK_ENGINE, SOUND_SBK_IDLE, SOUND_SBK_HORN
    else:
        engines, idles, horns = D1_ENGINE_SAMPLE, None, D1_HORN_SAMPLE
    for rank, model in enumerate(sorted(models)):
        engine = engines[rank % len(engines)]
        horn = horns[rank % len(horns)]
        idle = idles[rank % len(idles)] if idles else None
        offsets[model] = len(body)
        body += build_bank(voices, engine, horn, idle)
        rows.append((model, engine, horn, idle))
    struct.pack_into("<%di" % MAX_CAR_MODELS, body, 8, *offsets)
    return bytes(body), rows


def insert_sound_segment(blob, sound_body):
    parsed = find_data1(blob)
    if parsed is None:
        raise SystemExit("not a synthetic LEV: no DATA1 table")

    data1_off, data1_size = parsed
    body_size = struct.unpack_from("<i", blob, data1_off + 4)[0]
    if body_size < 8 or data1_off + body_size > len(blob):
        raise SystemExit("bad DATA1 header size %d" % body_size)

    old_body = blob[data1_off + 8:data1_off + body_size]
    seg = struct.pack("<ii", LUMP_CAR_SOUNDS, len(sound_body)) + sound_body
    seg += b"\x00" * ((4 - len(seg) % 4) % 4)

    new_body = bytes(old_body) + seg
    new_body += b"\x00" * ((4 - len(new_body) % 4) % 4)
    new_data1 = struct.pack("<ii", LUMP_CAR_DATA, len(new_body) + 8) + new_body
    new_data1 += b"\x00" * ((2048 - len(new_data1) % 2048) % 2048)

    page_data = blob[data1_off + data1_size:]
    out = bytearray(blob[:data1_off])
    out += new_data1
    out += page_data
    struct.pack_into("<ii", out, 8, data1_off, len(new_data1))
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lev", required=True)
    ap.add_argument("--voices", default=None,
                    help="DRIVER/SOUND/VOICES.BLK (default: sibling of the LEV)")
    ap.add_argument("--out", default=None, help="write here (default: in place)")
    args = ap.parse_args(argv)

    voices_path = args.voices
    if voices_path is None:
        # DRIVER/D1CARS/*.LEV -> DRIVER/SOUND/VOICES.BLK
        voices_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(args.lev))),
            "SOUND", "VOICES.BLK")

    voices = read_d1_voices(voices_path)
    if voices is None:
        raise SystemExit("cannot read bank 0 from %s" % voices_path)

    blob = open(args.lev, "rb").read()
    parsed = find_data1(blob)
    if parsed is None:
        raise SystemExit("not a synthetic LEV: %s" % args.lev)

    models = present_models(blob, *parsed)
    if not models:
        raise SystemExit("no car models in %s" % args.lev)

    sound_body, rows = build_sound_body(voices, models)
    out = insert_sound_segment(blob, sound_body)

    dst = args.out or args.lev
    with open(dst, "wb") as f:
        f.write(out)

    print("patched %s: %d models, %d -> %d bytes" %
          (dst, len(rows), len(blob), len(out)))
    for model, engine, horn, idle in rows:
        if idle is None:
            print("   model %2d: D1 loop %d (engine + idle), horn %d" % (model, engine, horn))
        else:
            print("   model %2d: beta engine %d, idle %d, horn %d" % (model, engine, idle, horn))


if __name__ == "__main__":
    main()
