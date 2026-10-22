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

# The Euro-43 SOUND.SBK has its own bank order. The engine's composer takes the
# FIRST model bank's sample 0 as the engine, the NEXT bank's sample 0 as the
# idle (it only needs a different length), and the first bank's sample 2 as the
# horn. The beta's sample 01 is the engine and sample 00 the idle; the horns are
# the retail Driver 1 ones (VOICES.BLK 6/7/8), mixed into the same bank.
SOUND_SBK_ENGINE = 1     # 2,112 bytes @11025 Hz, looping
SOUND_SBK_IDLE = 0       # 3,200 bytes @11025 Hz, looping

# Per-model horn/siren map, by baked Driver 2 model slot. The engine loads each
# imported model's own 3-sample bank, so these are the actual third samples:
# 8 = retail VOICES.BLK big-car horn, 7 = beefier, 9 = smaller, 4 = retail cop
# siren (only on the police list), 7 = beta SOUND.SBK siren (BETAMIAMI cop).
POLICE_MODELS = {
    "MIAMI": (1,), "FRISCO": (1,), "LA": (1, 9), "NEWYORK": (1, 9),
    "IVIEW": (1,), "BETAMIAMI": (8,),
}
HORN8_MODELS = {
    "MIAMI": (4,), "FRISCO": (4,), "LA": (4,), "NEWYORK": (4,),
    "NEWCASTLE": (3,), "BETAMIAMI": (3, 4),
}
SMALL_MODELS = {"FRISCO": (3,), "BETAMIAMI": (1, 2)}
SIREN_SAMPLE_RETAIL = 4
SIREN_SAMPLE_BETA = 7


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


def build_bank(voices, engine_index, horn_index, idle_index=None,
               horn_voices=None, engine_rate=None):
    payload = bytearray()
    entries = []

    def append(index, src=None, rate=None):
        src = voices if src is None else src
        addr, length, loop, samplerate = src["entries"][index]
        if rate is not None:
            samplerate = rate
        entries.append((len(payload), length, loop, samplerate))
        payload.extend(src["vag"][addr:addr + length])

    append(engine_index, rate=engine_rate)
    if idle_index is None:
        entries.append(entries[-1])      # retail: idle shares the one D1 loop
    else:
        append(idle_index)               # beta bank carries its own idle
    append(horn_index, horn_voices)

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


def build_sound_body(voices, models, horn_voices=None, city=None):
    offsets = [-1] * MAX_CAR_MODELS
    body = bytearray(struct.pack("<ii", 1, MAX_CAR_MODELS))
    body += struct.pack("<%di" % MAX_CAR_MODELS, *offsets)
    rows = []
    city = city.upper() if city else None
    police = set(POLICE_MODELS.get(city, ()))
    horn8 = set(HORN8_MODELS.get(city, ()))
    small = set(SMALL_MODELS.get(city, ()))
    if voices.get("kind") == "sbk":
        # Every imported model gets its OWN 3-sample bank: engine, idle, horn.
        # The engine loads these per model, so the third sample is the actual
        # per-slot horn (or the cop siren), not one city-wide sample.
        if horn_voices is None:
            raise SystemExit("the beta SOUND.SBK needs a retail horn bank")
        for model in sorted(models):
            engine = SOUND_SBK_ENGINE
            idle = SOUND_SBK_IDLE
            engine_rate = voices["entries"][engine][3] * 2 // 3   # -33% pitch (2/3 rate)
            if model in police:
                horn = SIREN_SAMPLE_BETA
                horn_src = None          # the beta bank's own siren sample
            else:
                horn = 8 if model in horn8 else (9 if model in small else 7)
                horn_src = horn_voices
            offsets[model] = len(body)
            body += build_bank(voices, engine, horn, idle,
                               horn_voices=horn_src, engine_rate=engine_rate)
            rows.append((model, engine, horn, idle))
    else:
        # Keep the city-wide engine/idle pair the old generic composer used:
        # engine = the first model's engine loop, idle = the next model's engine
        # loop (a different loop length). Only the horn is per model now.
        engine = D1_ENGINE_SAMPLE[0]
        idle = D1_ENGINE_SAMPLE[1]
        for model in sorted(models):
            if model in police:
                horn = SIREN_SAMPLE_RETAIL
            elif model in horn8:
                horn = 8
            elif model in small:
                horn = 9
            else:
                horn = 7
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
    ap.add_argument("--horns", default=None,
                    help="retail VOICES.BLK supplying the horns for a beta SOUND.SBK "
                         "(default: DRIVER/SOUND/VOICES.BLK beside the LEV)")
    ap.add_argument("--city", default=None,
                    help="city name; makes the generic horn the cop siren for "
                         "MIAMI/FRISCO/LA/NEWYORK/IVIEW (sample 4) or BETAMIAMI (sample 7)")
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

    horn_voices = None
    if voices.get("kind") == "sbk":
        horns_path = args.horns
        if horns_path is None:
            horns_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(args.lev))),
                "SOUND", "VOICES.BLK")
        horn_voices = read_d1_voices(horns_path)
        if horn_voices is None:
            raise SystemExit("cannot read retail horn bank from %s" % horns_path)

    blob = open(args.lev, "rb").read()
    parsed = find_data1(blob)
    if parsed is None:
        raise SystemExit("not a synthetic LEV: %s" % args.lev)

    models = present_models(blob, *parsed)
    if not models:
        raise SystemExit("no car models in %s" % args.lev)

    sound_body, rows = build_sound_body(voices, models, horn_voices, args.city)
    out = insert_sound_segment(blob, sound_body)

    dst = args.out or args.lev
    with open(dst, "wb") as f:
        f.write(out)

    print("patched %s: %d models, %d -> %d bytes" %
          (dst, len(rows), len(blob), len(out)))
    for model, engine, horn, idle in rows:
        if idle is None:
            print("   model %2d: engine %d, horn %d" % (model, engine, horn))
        else:
            print("   model %2d: engine %d, idle %d, horn %d" % (model, engine, idle, horn))


if __name__ == "__main__":
    main()
