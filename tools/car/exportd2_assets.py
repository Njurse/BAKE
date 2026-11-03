#!/usr/bin/env python3
"""exportd2_assets.py - the rest of a Firebird-shaped vehicle package.

exportd2.export_one writes the models (art/*.obj), pages (art/textures/PAGE_N.png)
and the donor lumps. This fills in everything else a bank.json `declare` can
carry, so a car can be edited and rebaked from obj/png/wav/json alone:

  wheels    the page's WHEEL/WHLCLEAN/WHLBOTH sprite cropped to
            art/textures/wheel{clean,damaged,fast}.png + set/island
  sounds    the car's engine/idle/horn bank out of DRIVER2/SOUND/VOICES2.BLK
            (gamesnd.c car_banks / MapCarModelToBank), decoded to WAV
  paints    best effort from LUMP_PALLET: the dominant colour of each civ
            palette column per page, colours shared by every column = protect
  handling  cars/<car>/handling.json - the LCF record's named fields
"""
import collections
import json
import os
import struct

import sbk_samples
import vag

CITY_INDEX = {"CHICAGO": 0, "HAVANA": 1, "VEGAS": 2, "RIO": 3}

# gamesnd.c car_banks[city][MapCarModelToBank row]
CAR_BANKS = [
    [12, 4, 13, 5, 16, 9, 2, 15, 10],
    [10, 11, 7, 6, 17, 17, 2, 5, 4],
    [14, 3, 4, 6, 11, 9, 2, 17, 10],
    [8, 7, 13, 9, 2, 17, 17, 11, 16],
]
SOUND_ROLES = ("engine", "idle", "horn")


def sound_lump(city, model):
    """gamesnd.c MapCarModelToBank, or None for a city with no table."""
    ci = CITY_INDEX.get(city)
    if ci is None:
        return None
    ret = model - 1
    if model == 0:
        ret = 1
    if ret > 6:
        ret -= 3
    ret = max(0, min(8, ret))
    return CAR_BANKS[ci][ret]


def find_voices2(lev_path):
    levels = os.path.dirname(os.path.abspath(lev_path))
    for cand in (os.path.join(levels, "..", "SOUND", "VOICES2.BLK"),
                 os.path.join(levels, "SOUND", "VOICES2.BLK")):
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    return None


def read_voices2_lump(path, lump):
    with open(path, "rb") as f:
        d = f.read()
    limits = struct.unpack_from("<73I", d, 0)
    off, end = limits[lump], limits[lump + 1]
    bank = d[off:end]
    n = struct.unpack_from("<i", bank, 0)[0]
    vag_data = bank[4 + n * 16:]
    out = []
    for i in range(n):
        a, length, loop, rate = struct.unpack_from("<IIII", bank, 4 + i * 16)
        out.append({"pcm": sbk_samples.decode_vag(vag_data[a:a + length]),
                    "rate": rate, "loop": bool(loop), "bytes": length})
    return out


def export_sounds(city_dir, city, car_id, model, lev_path, warnings, cache):
    voices = cache.setdefault("voices2", find_voices2(lev_path))
    if not voices:
        warnings.append("%s: no SOUND/VOICES2.BLK beside the LEVELS folder; "
                        "sounds not exported" % car_id)
        return None
    lump = sound_lump(city, model)
    if lump is None:
        warnings.append("%s: %s has no car_banks row; sounds not exported"
                        % (car_id, city))
        return None
    samples = cache.setdefault(("lump", lump), None) or read_voices2_lump(voices, lump)
    cache[("lump", lump)] = samples
    if len(samples) < 3:
        warnings.append("%s: VOICES2 lump %d has %d samples, not 3"
                        % (car_id, lump, len(samples)))
        return None
    # one car per bank (the default) keeps Firebird's flat sounds/engine.wav
    rel_dir = "cars/%s/sounds" % car_id
    os.makedirs(os.path.join(city_dir, rel_dir), exist_ok=True)
    decl = {}
    for role, s in zip(SOUND_ROLES, samples):
        rel = "%s/%s.wav" % (rel_dir, role)
        sbk_samples.write_wav(os.path.join(city_dir, rel), s["pcm"], s["rate"])
        # retail's own rate and level, so a rebake from the WAV sounds the same
        decl[role] = {"wav": rel, "rate": s["rate"], "normalise": False,
                      "loop": s["loop"]}
    decl["max_rate"] = max(s["rate"] for s in samples[:3])
    decl["sample_bytes"] = max(16384, max(s["bytes"] for s in samples[:3]))
    return decl, {"voices2": voices, "lump": lump,
                  "samples": [{"role": r, "rate": s["rate"], "loop": s["loop"],
                               "bytes": s["bytes"]}
                              for r, s in zip(SOUND_ROLES, samples)]}


WHEEL_FILES = {"clean": "wheelclean.png", "damaged": "wheeldam.png",
               "fast": "wheelfast.png"}
# which detail name serves which variant, best first
WHEEL_SOURCES = {"clean": ("WHLCLEAN", "WHEEL", "WHLBOTH"),
                 "damaged": ("WHLBOTH", "WHEEL", "WHLCLEAN"),
                 "fast": ("WHEEL", "WHLBOTH", "WHLCLEAN")}


def write_car_mtls(city_dir, cars, materials, page_rel):
    """cars/<car>/<car>.mtl, each map_Kd pointing at wherever its page lives."""
    for c in cars:
        car_dir = os.path.join(city_dir, "cars", c["id"])
        with open(os.path.join(car_dir, "%s.mtl" % c["id"]), "w") as f:
            f.write("# exportd2 materials\nnewmtl flat\nKd 0.5 0.5 0.5\n\n")
            for key in sorted(materials):
                if key == "flat" or materials[key][0] not in c["sets"]:
                    continue
                setid, texid = materials[key]
                rel = os.path.relpath(os.path.join(city_dir, page_rel.get(
                    setid, "shared/textures/PAGE_%d.png" % setid)), car_dir)
                f.write("newmtl %s\nKd 1.000 1.000 1.000\nmap_Kd %s\n\n"
                        % (key, rel.replace("\\", "/")))


def export_wheels(city_dir, page_rel, details, included_sets, warnings):
    from PIL import Image
    found = {}
    for pi, d in details:
        if pi in included_sets and d.get("name") and d["name"] not in found:
            found[d["name"]] = (pi, d)
    decl = {}
    for variant, names in WHEEL_SOURCES.items():
        hit = next((found[n] for n in names if n in found), None)
        if hit is None:
            continue
        pi, d = hit
        island = [d["x"], d["y"], d["x"] + d["width"], d["y"] + d["height"]]
        page = os.path.join(city_dir, page_rel[pi])
        rel = "shared/textures/" + WHEEL_FILES[variant]
        os.makedirs(os.path.join(city_dir, "shared", "textures"), exist_ok=True)
        Image.open(page).crop(tuple(island)).save(os.path.join(city_dir, rel))
        decl[variant] = {"png": rel, "set": pi, "island": island}
    if not decl:
        warnings.append("no WHEEL sprite in the texture details; wheels not "
                        "declared (the host level's wheels are kept)")
    return decl


def _hex(rgb):
    return "%02x%02x%02x" % tuple(rgb)


def recover_paints(palette_records, sets):
    """{set: {"paints": {...}, "protect": [...]}} - an approximation of the
    retail civ palettes as Firebird-style single colours."""
    out = {}
    for s in sets:
        cols = collections.defaultdict(collections.Counter)
        for r in palette_records:
            if r["tpageindex"] != s or not r.get("clut_rgb"):
                continue
            for c in r["clut_rgb"][1:]:
                cols[r["palette"]][_hex(c)] += 1
        if len(cols) < 2:
            continue
        shared = set.intersection(*[set(c) for c in cols.values()])
        paints = {}
        for p in sorted(cols)[:5]:
            own = [(n, h) for h, n in cols[p].items() if h not in shared]
            if own:
                paints["paint%d" % p] = max(own)[1]
        top_shared = sorted(shared, key=lambda h: -sum(c[h] for c in cols.values()))
        out[s] = {"paints": paints, "protect": top_shared[:8]}
    return out


def write_handling(city_dir, cars, cos_records, fields_fn):
    for c in cars:
        m = c["model"]
        d = os.path.join(city_dir, "cars", c["id"])
        os.makedirs(d, exist_ok=True)
        if m < len(cos_records):
            with open(os.path.join(d, "handling.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"model": m, "fields": fields_fn(cos_records[m]),
                           "raw": cos_records[m].hex(),
                           "note": "4096 = 1.0 for mass/traction/susCoeff/"
                                   "powerRatio; edit cars/<car>/handling.json "
                                   "(or this record) and rebake"},
                          f, indent=1)


def export_siren(city_dir, city, lev_path, warnings):
    """The city's stock cop siren (gamesnd.c CopSirenBankForCity) as
    sounds/siren.wav. In Driver 2 the siren is NOT in the car's bank: the game
    loads it into SOUND_BANK_VOICES from VOICES2.BLK lump 69+, so this is a
    reference/edit copy. To ship a custom siren on a car, declare on that car
    in bank.json: "sounds": {..., "cop": true, "siren": "sounds/siren.wav"}
    (carbank_bake writes it as the third sample + a LUMP_CAR_SIRENS flag;
    see carhacks/docs/SIRENS.md)."""
    ci = CITY_INDEX.get(city)
    voices = find_voices2(lev_path)
    if ci is None or not voices:
        return None
    lump = 69 + ((ci & 1) * 2 if ci & 2 else (ci & 3))
    samples = read_voices2_lump(voices, lump)
    if not samples:
        warnings.append("siren lump %d is empty" % lump)
        return None
    s0 = samples[0]
    os.makedirs(os.path.join(city_dir, "shared", "sounds"), exist_ok=True)
    sbk_samples.write_wav(os.path.join(city_dir, "shared", "sounds", "siren.wav"),
                          s0["pcm"], s0["rate"])
    return {"wav": "shared/sounds/siren.wav", "voices2_lump": lump,
            "rate": s0["rate"], "loop": s0["loop"]}
