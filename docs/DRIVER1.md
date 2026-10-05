# Driver 1 cars — format work and roadmap

**Status: design / Phase 1.** Only the module scaffold exists. This file is the
measured starting point for the rest: where Driver 1 keeps its car geometry, how
its format differs from the Driver 2 format the engine already consumes, and what
has to change to offer the shapes as a *fifth* car-data source.

Everything below was either measured off this install's bytes or read out of the
engine source; each claim says which. Line references are into
`src_rebuild/Game/` unless stated.

---

## 0. Goal

Lift the **civilian traffic car shapes** out of the original Driver 1 (1999, PSX)
level files and offer them to REDRIVER2 as a car-data source that sits
**alongside** the four Driver 2 cities (Chicago / Havana / Rio / Vegas) — a fifth
bucket, not a replacement for one. First milestone is a geometry-only "blockout"
pool: the shapes render, untextured.

Two questions had to be answered before any code: **where D1 keeps its car
geometry**, and **how far its format is from the one the engine eats**. Both are
answered below with the bytes.

---

## 1. Where the Driver 1 data is

The 1999 PSX disc tree is checked out under the build output:

```
src_rebuild/bin/Release_dev/DRIVER/
  LEVELS/{NEWYORK,MIAMI,LA,FRISCO,FLAT,TRAIN,CREDITS,IVIEW}.LEV
  DT{C,F,I,L,M,N}.BIN                 (6484 bytes each)
  CRED.BIN  FRONTEND.BIN  IVIEW.BIN  MCARD.BIN
  DATA/  GFX/  FRNT/  FB/  MISSIONS/  NFMV/  REPLAYS/  SOUND/  XA/
```

The four **city** levels are New York / Miami / LA / Frisco — that is how we know
this is Driver 1 and not a second Driver 2 install.

> **Local-only asset.** `src_rebuild/bin/*` is gitignored (repo `.gitignore`), so
> this tree is not in a fresh clone. Any tool that reads it must take the path as
> an argument, never assume it.

---

## 2. Where the car geometry lives

**Inside each `LEVELS/*.LEV`.** There is no shared car archive — the DRIVER root
holds only `CRED/FRONTEND/IVIEW/MCARD` `.BIN`s plus the per-level `DT*.BIN`
sidecars (§5).

*External confirmation:* the OpenDriver2Tools Driver 1 loader exposes
`LoadCarModelsLump()` and `LoadModelNamesLump()` and a
`CarModelData_t { cleanmodel, dammodel, lowmodel }`; a Driver Madness hex dump of
`MIAMI.LEV` shows the model-name strings `CIVCAR1LOW`, `COPCARCLEAN`, `COPDAM`,
`COPLOW`, `PLAYERCAR1CLEAN/DAM/LOW`.

So a "pool of traffic car shapes" = read the car-models lump out of each level
and dedupe.

---

## 3. The two formats, side by side

| | Driver 1 (1999, PSX) | Driver 2 / REDRIVER2 |
|---|---|---|
| Level container | `LEVELS/*.LEV` opens `25 00 00 00 f8 07 00 00` then a table; body at `0x800` is type **35** (`LUMP_LEVELDESC`) — **same container family as D2** | same `type/size` container: header `25` (37) + size, then `citylumps[8]` (`FORMATS.md` §1) |
| Model table | per-level car index → `{clean, damaged, low}` + a names lump | `LUMP_CAR_MODELS` (id **28**): a fixed **13-model × 3-int32** table (clean/damaged/low; `-1` = absent), data at `+164`, plus `LUMP_PALLET` (id **25**) |
| Poly record | **uncompressed** PSX-style record with an explicit texture **`page` byte** + per-vertex UV/normal indices + colour (`dpoly_t`) | `POLYFT3`/`POLYFT4`/`POLYGT3`/`POLYGT4` whose `texture_set` + `texture_id` are **indices** into runtime arrays |
| Palette | per-level CLUTs in the `.LEV` header | `LUMP_PALLET` → `civ_clut`, resolved through per-city `carTpages[4][8]` |

**Measured** (this install): `FRISCO.LEV` begins
`25 00 00 00  f8 07 00 00 | 00 08 00 00 | 00 58 01 00 | …`
= `(37, 2040, 2048, 88064, 90112, 440320, 530432, 231424, 761856, 6502400)`, and
the body at `0x800` is `(type 35, size 86876)`. Driver 2 was built by the same
studio on the Driver 1 engine, so the **container** is shared — but the **car
records** inside are not.

---

## 4. What the engine actually consumes (why an offline bake can target it)

The engine reads a car-model blob and **walks its poly stream** — this is the
whole walk:

```c
// cars.c:1792
polyList += PolySizes[ptype & 0x1f];
```

with `PolySizes[56]` (`draw.c:39`) and the poly structs in `engine/mdl.h`:

```c
struct POLYFT3 { u_char id, texture_set, texture_id, spare;
                 u_char v0,v1,v2,pad; UV_INFO uv0,uv1,uv2,pad2; RGB color; };
struct POLYGT3 { /* … as FT3 but with n0,n1,n2 (per-vertex normals) … */ };
```

A `MODEL` header (`mdl.h`) carries `num_polys`, `poly_block`, `vertices`,
`normals`. The target is therefore a **plain primitive stream** — the same class
of structure as Driver 1's `dpoly_t`, which is what makes an offline D1→engine
bake tractable.

The blob is found and consumed by the **existing cross-city path**. carhacks reads
a foreign city's `DATA1` region and pulls three lumps by id:

```c
// models.c:234-236
CAR_IMPORT_LUMP_MODELS   28   // LUMP_CAR_MODELS     - the geometry
CAR_IMPORT_LUMP_PALLET   25   // LUMP_PALLET         - the car palettes
CAR_IMPORT_LUMP_TEXINFO  34   // LUMP_TEXTUREINFO
```

via `LoadCarImport` (`models.c:347`) + `FindLumpSegment` (`models.c:264`);
`ProcessCarModelLump` (`models.c:753`) builds each resident slot's geometry, with
the data area at `lump_ptr + 4 + 160` and three int32 offsets per model
(clean, damaged, low).

**Design consequence:** if the bake emits a blob in *this* shape, every line of
the import path is reused unchanged, and "a fifth source" reduces to a fifth row
in the tables that path already iterates. That is the whole reason the pipeline
is *offline bake → synthetic fifth city*.

---

## 5. `DT*.BIN` — what they are (and are not)

Six files, exactly **6484 bytes** each, beside six of the eight levels; the third
letter matches the level initial (`DTF`=Frisco, `DTN`=NewYork, `DTM`=Miami,
`DTL`=LA, `DTI`=Iview, `DTC`=Credits). Like the `.LEV`s they are `0xFF`-terminated
rows of ascending byte values, reaching `0xF1` (= 241).

They are *probably* per-level tables of byte-sized indices — plausibly the
traffic-car lists — but that is **unverified**: no public documentation of
`DT*.BIN` exists, so this is our reading of the bytes, not a spec.

**They are not needed for a geometry-first milestone** and are parked.

---

## 6. The structural work: "four cities" is baked in

The hard part is not the file I/O — it is the bucket. "Four" is written into a
row of data tables and a set of `[4]` arrays. Each of these must grow to five:

**Data tables (add a row):**

| what | where |
|---|---|
| `LevelNames[]`, `LevelFiles[]` | `system.c:130`, `system.c:137` |
| `CosmeticFiles[]` | `cosmetic.c:14` |
| `carTpages[4][8]`, `specTpages[4][12]` | `texture.c:72`, `texture.c:37` |
| `CarAvailability[4][10]`, `carNumLookup[4][10]`, `minmaxSelections[4][2]` | `Frontend/FEmain.c:632`, `:639`, `:646` |
| `bootLevelNames[4]` | `main.c:3099` |

**Hard limits (the real ceiling):**

| limit | where |
|---|---|
| `#define CHK_CITY_COUNT_LIMIT 4` | `MODS/carhacks/carid.h:25` |
| `#define CHK_CAR_CITY_COUNT 4` | `MODS/carhacks/carimport.h:92` |
| `static CAR_IMPORT gCarImports[4]` + `city >= 4` guards | `models.c:258`, `:356`, `:450`, `:463`, `:502` |
| `static int gCarModelSource[MAX_CAR_RESIDENT_MODELS]` guard | `mission.c:332` |
| `city >= CHK_CAR_CITY_COUNT` offer gate | `MODS/carhacks/carimport.c:477` |
| `carhacks.c:92`, `:342`, `:346` (`level>3`, `pickCity<4`, `src<4`) | `MODS/carhacks/carhacks.c` |
| `city >= 4` and the `imported < 4` loop | `cars.c:3113`, `:3175`, `:3240` |

**Frozen dimensions:** `exports.def` names several of these arrays *with their
dimensions* (`carTpages[4][8]`, `specTpages[4][12]`, `carNumLookup[4][10]`,
`CarAvailability[4][10]`). Changing a header's dimension without the matching
`exports.def` edit breaks the link in every configuration — they must move in the
same commit.

**Free real estate:** the resident-slot pool is `MAX_CAR_RESIDENT_MODELS 12`
(`Game/dr2limits.h:35`) with `SPECIAL_CAR_SLOT 11` (`:40`); slots **5..10** are
already module spares (`mission.c:357` `SetupResidentModels`), which is where pool
items belong.

---

## 7. Constraints and open questions

- **Pool vs the 13-model table.** The engine's per-city car-model table is fixed
  at 13 models, 0..12 (`FORMATS.md` §3). Driver 1's traffic IDs run past 200. So
  the pool is an **archive** you select a few items from into the ~12 resident
  slots — it can never be "all D1 traffic at once".
- **The "compressed" claim.** `FORMATS.md` §8 says car-model polys are compressed
  and a `PolySizes` walk *stalls from polygon 5* — yet the engine walks them with
  exactly `PolySizes[ptype & 0x1f]` (`cars.c:1792`). Both cannot be literally
  true. **Resolve before the bake** by round-tripping one real D2 model through
  our reader and the engine's walk (Phase 2).
- **Textures.** A D1 poly carries a raw `page` byte; an engine poly carries
  `texture_set`, an *index* resolved per level through `texture_pages[128]` /
  `texture_cluts[128][32]` (`FORMATS.md` §2). A geometry-only blockout can
  sidestep this; real colour cannot, and the CLUT column is the known
  single-source limit (`MODS/carhacks/docs/CROSS_CITY.md`, `VRAM.md`).
- **`exports.def`.** Any exported array whose dimension changes needs the
  matching `exports.def` line in the same commit (the export-generation rule).

---

## 8. Roadmap

The approved phases. Only Phase 1 is done.

1. **Stand up the submodule + this doc.** — *done.* The module builds, registers
   its metadata and does nothing else.
2. **D1 level reader + car-model extractor** (`tools/lev1.py`). Walk
   `LEVELS/*.LEV`, decode the car-models and model-names lumps into a neutral
   model; reconcile the compression question (§7) against a real D2 model.
3. **Build the pool** (`tools/d1pool.py`). Extract every `CIVCAR*` shape from all
   eight levels, dedupe into one catalogue, emit `.obj` + `geom.json` +
   `pool.json`.
4. **Bake to a synthetic fifth city** (`tools/bake.py`) and extend the engine to a
   fifth source (the §6 tables/limits, in one commit with `exports.def`).
5. **Category it separately** — its own roster row so the pool is grouped apart
   from the four cities — plus headless acceptance (`crosscheck.py`,
   `hostdiff.py`).

---

## 9. How to re-measure (read-only)

```python
import struct
b = open('LEVELS/FRISCO.LEV', 'rb').read()
struct.unpack_from('<10i', b, 0)     # (37, 2040, 2048, 88064, 90112, …)  header + table
struct.unpack_from('<ii',  b, 0x800) # (35, 86876)                       LUMP_LEVELDESC body
```

```bash
stat -c '%s %n' DT*.BIN              # 6484 each
```

Run from `src_rebuild/bin/Release_dev/DRIVER` (gitignored, local-only — §1).

---

## 10. Sources

- **Local bytes:** `src_rebuild/bin/Release_dev/DRIVER/**` (this install).
- **Engine:** `Game/C/{models,cars,texture,system,cosmetic,mission,main}.c`,
  `Game/C/{carid,carimport,dr2limits}.h`, `Game/Frontend/FEmain.c`,
  `Game/engine/mdl.h`, `Game/C/draw.c`.
- **D2 side, measured:** `MODS/carhacks/docs/FORMATS.md`, `CROSS_CITY.md`, `VRAM.md`.
- **External:** OpenDriver2Tools `DriverLevelTool/driver_routines/models.h`
  (`LoadCarModelsLump`, `CarModelData_t`) and `regions_d1`; Driver Madness forum
  threads 2519 (D1 `.LEV` contents) and 2445 / 2420 (traffic-car modding).
  **There is no public spec for the D1 `.LEV` or `DT*.BIN`** — the D1 claims
  above rest on the loader source plus a forum hex dump, and the `DT*.BIN`
  reading is ours alone.
