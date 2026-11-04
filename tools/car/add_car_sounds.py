#!/usr/bin/env python3
"""add_car_sounds.py - add LUMP_CAR_SOUNDS to an existing synthetic D1 LEV.

The bake that writes JERICHO/MODS/carhacks/D1CARS/*.LEV also writes the car
geometry, palettes and wheels. Re-running that whole bake just to add sounds can
change the car mesh (a different bake produces a different model), so this tool
patches ONLY the sound segment into a known-good LEV. It reads bank 0 of
VOICES.BLK, builds one D2-shaped 3-sample bank per present model, appends the
type-201 segment to DATA1 and moves the page data to the new pageBase. Geometry
bytes are untouched.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))

import vag  # noqa: E402

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

# Beta engine/idle pitch, as a percentage of the SOUND.SBK sample's own rate.
# Two separate knobs so the engine loop and the idle loop can be tuned
# independently without touching each other.
BETA_ENGINE_PITCH = 60    # % of the raw sample rate
BETA_IDLE_PITCH = 75      # % of the raw sample rate

# Per-model horn/siren map, by baked Driver 2 model slot. The engine loads each
# imported model's own 3-sample bank, so these are the actual third samples:
# 8 = retail VOICES.BLK big-car horn, 7 = beefier, 9 = smaller, 4 = retail cop
# siren (only on the police list).
POLICE_MODELS = {
    "MIAMI": (1,), "FRISCO": (1,), "LA": (1, 9), "NEWYORK": (1, 9),
    "IVIEW": (1,), "BETAMIAMI": (8,),
}
HORN8_MODELS = {
    "MIAMI": (4,), "FRISCO": (4,), "LA": (4,), "NEWYORK": (4,),
    "NEWCASTLE": (3,), "BETAMIAMI": (3,),
}
SMALL_MODELS = {"FRISCO": (3,), "BETAMIAMI": (1,)}
SIREN_SAMPLE_RETAIL = 4

# Extra linear trim after the sample has been levelled to a stock Driver 2
# car bank (peak ceiling 0.999, rms 0.34 — see vag.DEFAULT_PEAK / DEFAULT_RMS).
# 1.0 means no extra trim. The old 0.45 was a peak scale: a limited loop
# (crest ~2) stayed loud and a dynamic one went quiet, and the encode then
# moved it again. Pass a number below 1 to sit a bank under retail on purpose.
SAMPLE_GAIN = 1.0


def decode_vag(data):
    """PSX VAG/ADPCM -> list of signed 16-bit samples."""
    coef = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
    nframes = len(data) // 16
    out = []
    hist1 = hist2 = 0
    for f in range(nframes):
        off = f * 16
        hdr = data[off]
        pred = (hdr >> 4) & 0x0F
        shift = hdr & 0x0F
        f0, f1 = coef[pred] if pred < len(coef) else (0, 0)
        for i in range(28):
            b = data[off + 2 + (i >> 1)]
            nib = (b >> 4) if (i & 1) else (b & 0x0F)
            if nib >= 8:
                nib -= 16
            s = (nib << 12) >> shift
            s += (hist1 * f0 + hist2 * f1 + 32) >> 6
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            hist2 = hist1
            hist1 = s
            out.append(s)
    return out


def encode_vag(samples):
    """Minimal VAG encoder. Predictor coefficients match decode_vag."""
    coef = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
    out = bytearray()
    hist1 = hist2 = 0
    i = 0
    while i < len(samples):
        block = samples[i:i + 28]
        if len(block) < 28:
            block += [0] * (28 - len(block))

        best_err = float("inf")
        best_pred = 0
        best_shift = 0
        best_nibbles = None

        for pred in range(len(coef)):
            f0, f1 = coef[pred]
            for shift in range(0, 13):
                err = 0
                h1, h2 = hist1, hist2
                nibbles = []
                for s in block:
                    pred_s = (h1 * f0 + h2 * f1 + 32) >> 6
                    diff = s - pred_s
                    q = diff >> (12 - shift) if shift < 12 else diff >> (shift - 12)
                    q = max(-8, min(7, q))
                    nibbles.append(q & 0xF)
                    recon = (q << 12) >> shift
                    recon += pred_s
                    recon = max(-32768, min(32767, recon))
                    h2, h1 = h1, recon
                    err += abs(s - recon)
                if err < best_err:
                    best_err = err
                    best_pred = pred
                    best_shift = shift
                    best_nibbles = nibbles

        out.append((best_pred << 4) | best_shift)
        out.append(0)
        for n in range(0, 28, 2):
            out.append((best_nibbles[n] & 0xF) | ((best_nibbles[n + 1] & 0xF) << 4))

        f0, f1 = coef[best_pred]
        for q in best_nibbles:
            recon = ((q if q < 8 else q - 16) << 12) >> best_shift
            recon += (hist1 * f0 + hist2 * f1 + 32) >> 6
            recon = max(-32768, min(32767, recon))
            hist2, hist1 = hist1, recon

        i += 28
    return bytes(out)


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


# Every model's bank re-uses the same few samples, and encode_vag is a
# brute-force search in pure Python, so re-encoding per model made the sound
# step take ~13x longer than needed. Cache the gain-scaled VAG per sample.
_VAG_CACHE = {}


def _scaled_vag(src, index, gain):
    addr, length = src["entries"][index][:2]
    raw = bytes(src["vag"][addr:addr + length])
    # Key on the bytes themselves: id() of a freed source can be reused by the
    # next city's, which would hand that city another city's sample.
    key = (raw, gain, "d2rms")
    cached = _VAG_CACHE.get(key)
    if cached is None:
        pcm = decode_vag(raw)
        # Match a stock D2 car bank. A sample already inside the 6% band is
        # kept byte for byte: re-quantising a VAG that is already on the
        # ADPCM lattice is what crushes a clean loop.
        levelled, _before, applied = vag.level(
            pcm, peak=vag.DEFAULT_PEAK, rms=vag.DEFAULT_RMS)
        trim = float(gain if gain is not None else 1.0)

        if trim != 1.0:
            levelled = vag.scale(levelled, trim)
            applied *= trim

        if applied == 1.0:
            cached = raw
        else:
            data, _count, _err = vag.encode_settled(levelled, loop=False)
            out = bytearray(data)
            # encode writes its own flags. Carry the source's ADPCM flags
            # (loop start 0x04, loop end 0x03, one-shot end 0x01) across
            # block for block. Without them the engine note never loops and
            # the SPU decoder runs off the sample's end (BETAMIAMI's bank,
            # last in SPU RAM, overran decodeSound's buffer and crashed).
            for b in range(min(len(raw), len(out)) // 16):
                out[b * 16 + 1] = raw[b * 16 + 1]
            if (len(out) >= 16 and not any(
                    out[b * 16 + 1] & 0x01 for b in range(len(out) // 16))):
                out[len(out) - 16 + 1] |= 0x01
            cached = bytes(out)
        _VAG_CACHE[key] = cached
    return cached


def build_bank(voices, engine_index, horn_index, idle_index=None,
               horn_voices=None, engine_rate=None, idle_rate=None):
    payload = bytearray()
    entries = []

    def append(index, src=None, rate=None, gain=SAMPLE_GAIN):
        src = voices if src is None else src
        addr, length, loop, samplerate = src["entries"][index]
        if rate is not None:
            samplerate = rate

        block = _scaled_vag(src, index, gain)
        entries.append((len(payload), len(block), loop, samplerate))
        payload.extend(block)

    append(engine_index, rate=engine_rate)
    if idle_index is None:
        entries.append(entries[-1])      # retail: idle shares the one D1 loop
    else:
        append(idle_index, rate=idle_rate)  # beta bank carries its own idle
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
        if horn_voices is None:
            raise SystemExit("the beta SOUND.SBK needs a retail horn bank")
        for model in sorted(models):
            engine = SOUND_SBK_ENGINE
            idle = SOUND_SBK_IDLE
            engine_rate = voices["entries"][engine][3] * BETA_ENGINE_PITCH // 100
            idle_rate = voices["entries"][idle][3] * BETA_IDLE_PITCH // 100
            if model in police:
                horn = SIREN_SAMPLE_RETAIL
                horn_src = horn_voices
            else:
                horn = 8 if model in horn8 else (9 if model in small else 7)
                horn_src = horn_voices
            offsets[model] = len(body)
            body += build_bank(voices, engine, horn, idle,
                               horn_voices=horn_src, engine_rate=engine_rate,
                               idle_rate=idle_rate)
            rows.append((model, engine, horn, idle))
    else:
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
                    help="DRIVER/SOUND/VOICES.BLK, or the beta's EURO43/SOUND/SOUND.SBK "
                         "(default: <install>/DRIVER/SOUND/VOICES.BLK)")
    ap.add_argument("--horns", default=None,
                    help="retail VOICES.BLK supplying the horns for a beta SOUND.SBK "
                         "(default: <install>/DRIVER/SOUND/VOICES.BLK)")
    ap.add_argument("--city", default=None,
                    help="city name; makes the generic horn the cop siren for "
                         "MIAMI/FRISCO/LA/NEWYORK/IVIEW (sample 4) or BETAMIAMI (sample 7)")
    ap.add_argument("--out", default=None, help="write here (default: in place)")
    args = ap.parse_args(argv)

    voices_path = args.voices
    if voices_path is None:
        base = os.path.dirname(os.path.abspath(args.lev))
        voices_path = os.path.join(base, "..", "..", "..", "..",
                                   "DRIVER", "SOUND", "VOICES.BLK")
        if not os.path.isfile(voices_path):
            voices_path = os.path.join(base, "..", "..", "SOUND", "VOICES.BLK")

    voices = read_d1_voices(voices_path)
    if voices is None:
        raise SystemExit("cannot read bank 0 from %s" % voices_path)

    horn_voices = None
    if voices.get("kind") == "sbk":
        horns_path = args.horns
        if horns_path is None:
            base = os.path.dirname(os.path.abspath(args.lev))
            horns_path = os.path.join(base, "..", "..", "..", "..",
                                      "DRIVER", "SOUND", "VOICES.BLK")
            if not os.path.isfile(horns_path):
                horns_path = os.path.join(base, "..", "..", "SOUND", "VOICES.BLK")
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