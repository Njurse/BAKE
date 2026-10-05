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

**Winding — measured, and it agrees.** `cross(u, v)` on a car poly points
**inward** for both games (every FRISCO / MIAMI and every CHICAGO model comes out
negative), i.e. Driver 1 and Driver 2 share the same winding convention. So the
bake needs **no index reversal**: the engine's backface culling will treat a
baked Driver 1 car exactly as it treats Driver 2's own cars. (This is also why
`tools/preview.py` had to negate the normal before shading — see its comment.)

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
- **The "compressed" claim — RESOLVED (Phase 2).** An earlier note in
  `FORMATS.md` said car-model polys are compressed and a `PolySizes` walk *stalls
  from polygon 5*. Measured: **false for car models.** Every CHICAGO model walks
  its full `num_polys` with 0 zero-steps using only types 20 (FT3) and 22 (GT3),
  exactly as `cars.c:1792` and `texture.c` (`CollectModelSets`) do it — and D1's
  car models walk the same way (all 111 variants, 0 errors). `FORMATS.md` has
  been corrected. The only real caveat is to **bound the walk to `num_polys`**;
  walking past it is what produces a zero-size step, not compression.
- **Textures.** A D1 poly carries a raw `page` byte; an engine poly carries
  `texture_set`, an *index* resolved per level through `texture_pages[128]` /
  `texture_cluts[128][32]` (`FORMATS.md` §2). A geometry-only blockout can
  sidestep this; real colour cannot, and the CLUT column is the known
  single-source limit (`MODS/carhacks/docs/CROSS_CITY.md`, `VRAM.md`).
- **`exports.def`.** Any exported array whose dimension changes needs the
  matching `exports.def` line in the same commit (the export-generation rule).

---

## 8. Scale and cosmetics (Phase 3)

**Measured** by `tools/scalemodels.py` on this install (both games keep a car's
`MODEL` at `car_lump_body + 164 + offset`):

| | models | width (median) | height | length | `bounding_sphere` |
|---|---|---|---|---|---|
| Driver 1 | 44 clean (111 with all variants), 7 levels | 400 | 254 | 1104 | 30..644 |
| Driver 2 | 39, 4 cities | 284 | 176 | 752 | 223..805 |

D1/D2 median length ratio **1.468**, width ratio **1.408**.

**The factor is principled, not a fudge.** A Driver 1 level cell is **3000**
world units; a Driver 2 cell is **2048** — read per city from that city's
`LUMP_MAP` (`map.c:76`): CHICAGO / HAVANA / RIO / VEGAS all report
`cell_size = 2048`. The same physical car is therefore modelled `3000/2048`
larger in Driver 1's units, and scaling D1's medians by the reciprocal lands on
Driver 2's:

```
bake scale = 2048 / 3000 = 0.68267
  width  400 * 0.68267 = 273   (D2 284)
  height 254 * 0.68267 = 173   (D2 176)
  length 1104 * 0.68267 = 754  (D2 752)
```

**Decision.** The bake multiplies every D1 model vertex by `2048/3000` so a D1
shape lands in Driver 2's car size band. It is a named constant with the table
above behind it, and it travels in the pool record (`scale`) so an item can be
re-tuned without touching code. The damaged/low variants land in the same band
and use the same factor.

**The per-item plan is reproducible:** `tools/scalemodels.py --plan` prints
`d1 level | idx | D1 bbox | scaled bbox | nearest D2 car | scale` for every
item. A sample (the tool prints all 44):

| d1 level | idx | D1 bbox | scaled | nearest D2 car |
|---|---|---|---|---|
| FRISCO | 0 | 388x286x1134 | 265x195x774 | VEGAS model 2 (284x152x764) |
| FRISCO | 1 | 392x234x1066 | 268x160x728 | CHICAGO model 4 (319x262x724) |
| MIAMI | 2 | 410x254x1070 | 280x173x730 | CHICAGO model 4 (319x262x724) |
| IVIEW | 2 | 462x292x1202 | 315x199x821 | CHICAGO model 9 (418x322x846) |

Every scaled D1 model lands within a few percent of an existing D2 car, which is
the practical check that 2048/3000 is right. (CREDITS model 0, 52x49x48 →
35x33x33, is the non-car outlier; the pool excludes it.)

**Cosmetics (`CAR_COSMETICS`) — decided.** Driver 1 has no equivalent table, so
the pool **synthesizes** one per item:

- `colBox` and `cog` come from the model's **scaled bbox**, so the collision box
  and the centre of gravity match the shape;
- the rest — `wheelSize` / `wheelDisp`, the `cPoints` shadow corners, the light
  anchors (`headLight`/`brakeLight`/`revLight`/…), `susCoeff` / `powerRatio` /
  `mass` / twist — is cloned from a **donor Driver 2 car** picked by nearest
  scaled length.

It is emitted as an **`.LCF`-shaped blob** (`LEVELS\<pool>.LCF`), which is
exactly what the import path already reads: `LoadCarImport` pulls a city's
`.LCF` beside its car data (`models.c:429`), `GetCarImportCosmetics(slot)` hands
it to `ProcessCosmeticsLump` (`cosmetic.c:71`) and `JerHotLoadCarCosmetics`.
So the pool's cosmetics ride the existing cross-city machinery with **no engine
change**. An individual item can later be hand-tuned through the per-model
override `LEVELS\<CarDataFolder>\CARMODEL_<n>.COS` (`LoadCustomCarCosmetics`,
`cosmetic.c:33`).

---

## 9. Roadmap

The approved phases. **Phases 1–4 are done, and Phase 5's offline half is done;
the engine wiring and the test rig are NOT started.**

1. **Stand up the submodule + this doc.** — *done.* The module builds, registers
   its metadata and does nothing else.
2. **D1 level reader + car-model extractor** (`tools/lev1.py`). — *done.* Walks
   `LEVELS/*.LEV`, decodes every car-model variant into a neutral model, and its
   `--verify` reports 0 problems over all eight levels. The plan's "assert the
   CIVCAR* list" was retired: this D1 build names only level models, so a pool
   item's identity is (level, model index, variant).
3. **Scale & cosmetics** (`tools/scalemodels.py`). — *done, §8.* The bake scale
   is 2048/3000 (D1 cell 3000, D2 cell 2048), verified against the measured
   model sizes.
4. **Build the pool** (`tools/d1pool.py`). — *done.* 43 unique clean shapes from
   43 sources across the seven car-bearing levels (the levels share no shapes),
   emitted as `.obj` + `geom.json` + `pool.json`, with a preview sheet
   (`--render`) and a non-car outlier (CREDITS model 0) dropped.
5. **Bake to a synthetic fifth city** — *offline half done* (`tools/bake.py`):
   emits a blob in the exact shape the cross-city import reads (`models.c`
   `LoadCarImport`), flat F3/F4 polys so it is geometry-only, verified by
   re-reading it with an independent reader. **Still to do:** the engine wiring —
   a fifth row in `LevelNames`/`LevelFiles`/`CosmeticFiles`, `gCarImports[4]` to
   `[5]`, `CHK_CITY_COUNT_LIMIT`/`CHK_CAR_CITY_COUNT`, the `carTpages`/
   `specTpages` arrays, and every frozen `[4]` in `exports.def` — all in ONE
   commit (§6).
6. **Category it separately** — its own roster row so the pool is grouped apart
   from the four cities — plus a test rig on top of carhacks' checks
   (`crosscheck.py`, `hostdiff.py`, the identity line) and headless acceptance.
   **Not started.**

### Picking this up in a fresh session

1. The superproject gitlink was left one commit behind this submodule's HEAD at
   hand-off (`fix-car-switch-release`) — bump it first.
2. Phase 5's engine wiring as a single commit (§6 lists every site, and
   `exports.def` must move with it), then `premake5.exe vs2019` and a build.
3. Phase 6: make a pool item selectable through carhacks, add the roster row,
   and drive it through the existing harnesses.

---

## 10. How to re-measure (read-only)

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

## 11. Sources

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
