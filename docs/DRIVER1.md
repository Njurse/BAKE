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
bucket, not a replacement for one.

The offline side now goes past the first geometry-only "blockout" milestone: the
shapes render **textured**, lit, and smooth-shaded, and a baked blob carries its
own texture pages and palettes. What is left is the engine wiring and the test
rig (§9).

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

### The texture page, the palette and the normal tables (measured)

Three more pieces of the same file, all decoded now (`tools/pages1.py` and
`lev1.py`), because a car that carries its own textures needs them.

**A page entry**, as the engine's `LoadTPageAndCluts` reads one, is

    [int npalettes][npalettes x 32-byte CLUT rows][a decomp_asm stream]

and the pages are carved out of the tpage region (citylump 3) in the order of
the page list, each occupying `(size + 2047) & ~2047` bytes. `decomp_asm` is a
two-op RLE filling exactly `0x8000` bytes — 256x256 at 4bpp — writing BACKWARDS
from the end of the buffer. It is small enough to reimplement exactly: its
control byte copies `n+1` literal bytes below `0x80` and repeats the next byte
`258-n` times at or above it. The buffer is used as decoded, so the texel at
(x, y) is the `y*128 + x/2`th byte, low nibble for even x.

**`LUMP_TEXTUREINFO` (34)** is the page list. Its body is
`[tpage_amount][texamount][TP tpage_amount+1]`, then one length-prefixed `TEXINF`
array per page, then `[nperms][permlist]`, a **fixed 16-entry region**, then
`[nspecpages][speclist]`. The fixed region is the trap: the engine reaches the
special list through `&permlist[16]`, not `&permlist[nperms]`, so the slots a
short list does not use are padding.

**`LUMP_PALLET` (25)** is where Driver 1 and Driver 2 genuinely differ, so the
bake translates rather than copies. D1's record is **44 bytes**:

    int palette       colour-variety index (traffic cars use 0..3)
    int texnum        texture number within the page
    int tpageindex    the texture set (the page)
    32 bytes          16 RGB555 entries

and the header int is the RECORD count (`4 + n*44` equals the lump size on all
seven levels). D2's record is four ints whose fourth is a `clut_number`: `-1`
selects an inline CLUT, anything else references an earlier one, and the run ends
on a lone `-1`. D1 has no reference form and no terminator, because its count is
already exact.

**Two normal tables** sit between the vertices and the poly block, and they are
not interchangeable:

* the `normals` field points at `npol` entries, one per poly, each exactly that
  poly's geometric normal (measured 148/148, 200/200, 242/242 on FRISCO, zero
  flipped);
* the `point_normals` field points at `npn` entries, and **that** is the
  per-vertex table the GT record's normal indices index (`cars.c:650` indexes
  `nlist = point_normals` with them).

A model may ship no `point_normals` at all — 65 of the 111 variants do not — in
which case its GT indices have nothing to resolve against; the pool synthesises
one normal per vertex for those, indexed by the vertex, which is what smooth
shading means.

The stored normals are **inward** — `cross(u, v)`, pointing into the surface.
Measured rather than assumed: every FRISCO model's roof polys carry y = +1
(4096) and y is down-positive, so a stored roof normal points down into the car.
Negating is what makes the renderers agree with the geometry (dot > 0 on 96.5%
of samples, against 1.2% for the raw sign).

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

## 4a. The wheels travel with the car data

A Driver 1 car's wheels are **not part of its body mesh**. Every level names
`CLEANWHEEL`, `DAMWHEEL` and `FASTWHEEL` in its model table, and Driver 2's
engine finds those names once and draws them for every car - so an imported D1
car used to wear the *host level's* wheels, including its wheel texture.

The per-city bake now writes a `LUMP_CAR_WHEELS` segment (type **200**, which no
Driver 2 level writes) beside `LUMP_CAR_MODELS`:

    int32 count                      1..3
    int32 offsets[count]             relative to the segment body; -1 = absent
    per offset: MODEL + vertices + an FT4 poly stream

The three records are clean, damaged and fast. They are FT4 rather than GT3/GT4
because `DrawWheelObject` reads `POLYFT4` records directly and never uses
normals. Their texture pages are added to the same `LUMP_TEXTUREINFO` plan as
the car pages, and their set numbers go through the same bake-time renumbering
(the source level's set is meaningful only in that level).

`bake.py` picks a city's own wheels when they are car-scale and falls back to
`wheelgeom.canonical_wheel()` otherwise: MIAMI has no `DAMWHEEL`, and
NEWCASTLE's source (`CREDITS.LEV`) carries display-scale wheels, so both take
the canonical car-scale set rather than a wheel larger than the car.

The engine side is `GetCarImportWheel(city, kind)`: `models.c` copies the
records, applies the same `CarSetRemap` the car polys use, runs the host
wheel transform (`MangleWheelModel`), and returns NULL for an absent variant.
`DrawCarWheels` keeps the HOST level's wheel mesh and uses the imported model
only as the texture/UV source, so a D1 car keeps the proven wheel topology and
wears its own city's wheel texture and palette. `LoadImportedTPages` registers
the wheel's source sets against the slots that can draw them.

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
row of data tables and a set of `[4]` arrays. Each of these must grow to seven
(the five city levels, plus IVIEW and TRAIN, which carry car geometry of their own):

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
- **The D2-compliant-slot collision — the playtest blocker (measured).** Driver 1's
  `CAR_MODELS` table *is* its traffic-car list (7–8 cars per city; every entry
  decodes to car dimensions, ≈400×250×1100, matching §8's scale measurement). No
  D1 name names them — this build's `MODELNAMES` holds only `CLEANWHEEL`,
  `DAMWHEEL`, `FASTWHEEL`, `HELICOPTER` — so the **model index is the only
  identifier**. Measured layout:

  | city | models with a car | count |
  |---|---|---|
  | MIAMI / FRISCO / LA | `0,1,2,5,6,7,8` | 7 |
  | NEWYORK | `0,1,2,5,6,7,8,9` | 8 |

  Across the whole 43-shape pool the model indices in use are **0,1,2,5,6,7,8,9**
  (counts 6,7,7,7,5,5,5,1): **models 3 and 4 are never used in Driver 1**, while
  **5, 6 and 7 carry 17 of the 43 cars — 40%.**

  Driver 2's own compliant models are `0,1,2,3,4,8,9,10,11,12`; **5, 6 and 7 hold
  no data in any of the four cities** (`CHICAGO` ships 9 models, the others 10, and
  none ships 5/6/7). Its frontend then maps select-slots 0–3 → models **1,2,3,4**
  via `carNumLookup` (`FEmain.c:639`), with model 0 the police car at a locked
  slot. So a D1 city's cars land on models the engine will never address — the
  import resolves, nothing downstream names it, and the player silently keeps
  their own car.

  **Remedy: the transplant remaps.** A city's cars are written to D2-compliant
  models in order — the first four to **1,2,3,4** (mirroring a stock D2 city's
  four selectable slots) and any beyond that to **8,9,10,11** — with 5, 6, 7
  never written and model 0 left as D2's police car. See §8.
- **The "compressed" claim — RESOLVED (Phase 2).** An earlier note in
  `FORMATS.md` said car-model polys are compressed and a `PolySizes` walk *stalls
  from polygon 5*. Measured: **false for car models.** Every CHICAGO model walks
  its full `num_polys` with 0 zero-steps using only types 20 (FT3) and 22 (GT3),
  exactly as `cars.c:1792` and `texture.c` (`CollectModelSets`) do it — and D1's
  car models walk the same way (all 111 variants, 0 errors). `FORMATS.md` has
  been corrected. The only real caveat is to **bound the walk to `num_polys`**;
  walking past it is what produces a zero-size step, not compression.
- **Textures — decoded, and carried by the bake.** A D1 poly carries a raw
  `page` byte; an engine poly carries `texture_set`, an *index* resolved through
  `texture_pages[128]` / `texture_cluts[128][32]` (`FORMATS.md` §2). A
  geometry-only blockout sidestepped that; it no longer does. `tools/pages1.py`
  decodes the page entries and the palettes themselves (§3), `bake.py` emits
  them next to the models along with the page list, and `textured.py --baked`
  renders a baked blob from its own contents. What remains is the ENGINE side's
  two limits: the CLUT column is a single-source budget
  (`MODS/carhacks/docs/CROSS_CITY.md`, `VRAM.md`), and `carTpages[city]` holds
  only **8** sets while a full pool names **19** — so the engine wiring has to
  decide what a pool set maps onto.
- **`exports.def`.** Any exported array whose dimension changes needs the
  matching `exports.def` line in the same commit (the export-generation rule).
- **`texture_set 0` IS RESERVED — a bake must never number a car page 0.** The
  engine treats set 0 as "no page": `CarImportPin`'s walk skips it
  (`texture.c`, `if (set == 0 ...) continue`) and `CarSetRemap` re-indexes it onto
  a HOST/world slot, so every poly on it draws the level's own page instead of the
  car's. This was a live defect: the first version of this bake numbered the first
  used page 0, so NEWCASTLE model 1 had 75 of 135 polys (its hood among them) on a
  page that was never paged in, model 2 had 78 of 139, and model 3 only 20 of 233
  (its untextured ones) - which read as "the hood is invisible on car 1, cars 1
  and 2 are half broken, car 3 is fine". `bake.py` now starts at `PAGE_SET_BASE = 1`
  and the untextured F3/F4 polys (which carry set 0 legitimately) are DROPPED
  rather than emitted as GT3/GT4: no Driver 2 car model carries a flat poly at all.
  Verify with the `PERMS`/`SPECS` sets and a scan that no model poly has
  `texture_set == 0`.
- **A guest city gets EIGHT palette rows, and Driver 1 cities bake 11-13 sets.**
  `CarPalIndexInCity` resolves a set to a `civ_clut` row by searching
  `carTpages[city][0..7]`, so a set past the eighth answers `-1` and
  `CarPalIndexForBuild` sends it to the block base row: those panels draw their own
  pinned page but the FIRST page's palette. That is the visible "minor corruption
  on vehicles with extra panels" (VEGAS ambulance, large SUVs, long cars), and it
  is engine-side, not a bake bug. See `MODS/carhacks/docs/PALETTES.md` §7 for the
  two candidate fixes (fill `carTpages`' unused zero slots - D1 cities use only 5
  of the 8 - or assign rows per model at build time; 8 rows cannot hold 13 sets).

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

**Cosmetics (`CAR_COSMETICS`) — built; `tools/cosmetics1.py`.** Driver 1 has no
equivalent table at all, so every field is **derived**, one 236-byte record per
car.

**The record.** `dr2types.h:211` fixes the layout, and it is exactly 236 bytes:

| offset | bytes | field |
|---|---|---|
| 0..71 | 72 | `headLight frontInd backInd brakeLight revLight policeLight exhaust smoke fire` (9 × `SVECTOR`) |
| 72..103 | 32 | `wheelDisp[4]` |
| 104..115 | 12 | `extraInfo powerRatio cbYoffset susCoeff traction wheelSize` |
| 116..211 | 96 | `cPoints[12]` |
| 212..235 | 24 | `colBox cog twistRateX twistRateY twistRateZ mass` |

**The file.** `LoadCosmetics` (`cosmetic.c:148`) reads exactly **3120** bytes, and
`3120 = 52 + 13 × 236` to the byte — a 13-entry int32 offset table at 52, then the
records. Driver 2's own `.LCF`s are **4096** bytes on disk, zero-filled sector
padding that nothing reads, and this writer's output is *structurally identical*
to one: same 4096 size, offsets `52..2884`, zero tail (checked by diffing the
shape, not by assuming it). A D1 city ships fewer than 13 car models — FRISCO has
7 — so unused slots repeat **that city's own** first record rather than borrowing
a record from a city that needed a bus.

**Measured vs inherited.** Three groups are measured from the car itself: `colBox`
is its scaled bbox, `cog` is that box's centre, and `cPoints[12]` is its own
footprint — three heights (ground, belt, roof) by four corners, in Driver 2's own
order. `wheelDisp[4]` and `wheelSize` come from the wheel placement (§7). The nine
light/exhaust/smoke/fire anchors are the donor's, **re-proportioned per axis**
rather than cloned, which keeps each where Driver 2 authored it — headlight at the
front, exhaust at the rear — but at this car's own front/rear/height. The handling
scalars (`powerRatio` / `mass` / `traction` / `susCoeff` / twist) come from the
nearest-scaled-length donor.

**The wheel arithmetic is the one part that can be proved.** `cars.c:1273` places a
wheel as

```
x = ±17 - wheelDisp.vx      z = -wheelDisp.vz
y = -wheelSize - wheelDisp.vy - susCompression + 14
```

so `wheelDisp` is that inverted. Feeding Driver 2's own records through the forward
form and back returns Driver 2's own numbers for **all 208** of its wheel entries,
which settles the convention and the inversion together. Note what `wheelSize` is
**not**: it does not scale the wheel. `DrawWheelObject` draws the model at its
native size (`cars.c:1264`); the radius only sets the axle height.

**`FixCarCos` corrects the body's off-centre placement on load** (`handling.c:180`):
`delta.vz = -(wheelDisp[0].vz + wheelDisp[1].vz - 14) / 2`. With a placement
symmetric about the car's bbox z centre this reduces to `z_centre + 7`, so it is a
direct readout of how far off centre the body sits. Ours run −28..20 against
Driver 2's ordinary cars' −34..+6, so the engine has very little to correct.
`tools/cosmetics1.py --check` prints every field against Driver 2's own ranges side
by side.

**Where the four wheel centres come from — measured, and per car.** Neither number
is a constant, which is what Driver 2's own cars say:

| | Driver 2, ordinary cars | Driver 2, all 13 slots | ours |
|---|---|---|---|
| `wheelbase / half-length` | 1.04 .. 1.28 | 0.97 .. 1.60 | **1.04 .. 1.14** |
| sill vs tyre contact, in radii | −1.17 .. −0.66 | — | **−1.21 .. −0.63** |
| `wheelDisp.vy`, the axle height | −30 | −30 and **−116** (the vans) | per car |

So the placement takes both from a **matched Driver 2 car, re-proportioned per
axis onto our car's own bbox** — the same rule the light anchors already use, which
keeps one saying for "put this where Driver 2 put it, on a car this size". The
donor is matched on the **whole 3D proportion**, not length alone, because length
alone cannot tell a van from a saloon and the van is exactly the one whose axle
height is raised; the ordinary cars draw on donors 0, 1, 3 and 12.

Two consequences worth knowing:

- the body reaching *below* the tyre contact is Driver 2's own shape, not a bug —
  25..51% of its cars' vertices sit below that line;
- in the **previews** the wheels are pushed out to the body's side, because a D1
  body is a solid shell with no arches and a faithful tyre is hidden by the body's
  own side face. The file carries both: `centres` faithful for the records,
  `render_centres` for the renderers.

**Routing.** An `.LCF` is the natural home because it is exactly what the import
path already reads: `LoadCarImport` pulls a city's `.LCF` beside its car data
(`models.c:429`), `GetCarImportCosmetics(slot)` hands it to `ProcessCosmeticsLump`
(`cosmetic.c:71`) and `JerHotLoadCarCosmetics`. So the cosmetics ride the existing
cross-city machinery with **no engine change**. One file per D1 city is written —
`MIAMI.LCF`, `FRISCO.LCF`, `LA.LCF`, `NEWYORK.LCF` — and an individual car can
later be hand-tuned through the per-model override
`LEVELS\<CarDataFolder>\CARMODEL_<n>.COS` (`LoadCustomCarCosmetics`,
`cosmetic.c:33`).

---

## 9. Roadmap

The approved phases. The **offline half is done end to end**; **the engine
wiring and the test rig are NOT started** — everything described below runs
inside this submodule and touches no engine file.

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
   (`--render`) and a non-car outlier (CREDITS model 0) dropped. Each item now
   also carries, per poly, its texture naming (set, id, uv) and its normal
   indices and, per model, both normal tables with their provenance.
5. **Bake to a synthetic fifth city** — *offline half done* (`tools/bake.py`).
   It emits a blob in the exact shape the cross-city import reads (`models.c`
   `LoadCarImport`): the car models, `LUMP_PALLET` (translated into the engine's
   record shape, not copied), `LUMP_TEXTUREINFO` (the renumbered page list) and
   the page bytes themselves after DATA1, which is where `pageBase` looks. Every
   poly is emitted as GT3/GT4 carrying its texture set, id and uv **and** its
   normal indices, and every model ships both normal tables, so a baked car is
   smooth-shaded rather than faceted. Set numbers are renumbered per
   (source level, set) because a set number is per-level. Verified by re-reading
   the blob with an independent reader and rendering it textured from its own
   pages. **Still to do:** the engine wiring — a fifth row in
   `LevelNames`/`LevelFiles`/`CosmeticFiles`, `gCarImports[4]` to `[5]`,
   `CHK_CITY_COUNT_LIMIT`/`CHK_CAR_CITY_COUNT`, the `carTpages`/`specTpages`
   arrays, and every frozen `[4]` in `exports.def` — all in ONE commit (§6).
   One thing that wiring must decide, and the bake cannot: `carTpages[city]`
   holds **8** slots while a full pool names **19** sets, so the engine side has
   to say what a pool set maps onto, exactly as the CLUT column is the palette
   side's limit (§7).
6. **Category it separately** — its own roster row so the pool is grouped apart
   from the four cities — plus a test rig on top of carhacks' checks
   (`crosscheck.py`, `hostdiff.py`, the identity line) and headless acceptance.
   **Not started.**

### Previewing a baked blob

`preview.py`, `textured.py` and `spin.py` all read the baked blob directly.
`textured.py --baked` and `spin.py` take their pages from that blob's own
`LUMP_TEXTUREINFO` and page data — no DriverLevelTool export and no source level
read — so the baked path is self-supporting and is the one to trust when asking
"does the thing we would ship actually look right?". `spin.py` writes a rotating
GIF of every shape in one grid plus one GIF per model, textured and lit.

### Picking this up in a fresh session

1. The superproject gitlink is behind this submodule's HEAD (the superproject
   was deliberately left alone in the texture session) — bump it first.
2. Phase 5's engine wiring as a single commit (§6 lists every site, and
   `exports.def` must move with it), then `premake5.exe vs2019` and a build. The
   wiring also has to answer the two budget questions §7 raises: what a pool set
   maps onto given `carTpages[city]`'s 8 slots, and the CLUT column.
3. Phase 6: make a pool item selectable through carhacks, add the roster row,
   and drive it through the existing harnesses.

The offline side needs nothing: `bake.py` produces a blob that already carries
its own textures, palettes and page lists, and `textured.py --baked` / `spin.py`
show it.

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

```bash
T=JERICHO/MODS/d1cars/tools
L=src_rebuild/bin/Release_dev/DRIVER/LEVELS

python $T/pages1.py --selftest                    # the RLE, no game data needed
python $T/pages1.py $L/FRISCO.LEV --info          # page list + CLUT row counts
python $T/pages1.py $L/FRISCO.LEV --pallet        # 4 + n*44 == the lump size
python $T/pages1.py $L/FRISCO.LEV --write $T/out/pages

python $T/bake.py --pool $T/out/pool.json --first 13   # self-checks the normals
python $T/textured.py --baked $T/out/bake/D1POOL.LEV   # 0 polys missing a page
python $T/spin.py
```

Run from the repo root. Those three `pages1.py` modes are the page/palette facts
in §3 re-derived from the data rather than trusted.

Run from `src_rebuild/bin/Release_dev/DRIVER` (gitignored, local-only — §1).

---

## 11. The EURO-43 beta (BETAMIAMI)

`EURO43/LEVELS/BETAMIAMI.LEV` (the leaked Euro-43 build's Miami level) is Driver 1's
car data in a **headerless** stream: no `LUMPDESC`, no `DATA1`/`DATA2`
containers, just a flat sequence of `{type, size}` lumps starting with
`TEXTURENAMES` (5). The lump ids are the retail ones:

| offset | type | what |
|---|---|---|
| `0x1460` | 0 | `TEXTURES` — 75 texture pages **and** the texture-info header |
| `0x1581EC` | 25 | `PALLET` — 92 records, exact `4 + 92*44` |
| `0x15A9E0` | 1 | `MODELS` — 556 slots, the same `MODEL`/`POLYFT` records as retail |
| `0x1CBBF8` | 12 | `MODELNAMES` — 557 slot names |

The cars are **named level models**, not a type-28 `CAR_MODELS` table:
`CIVCAR1LOW`, `CIVCAR2LOW`, `COPCARCLEAN/DAM/LOW`, `PLAYERCAR1CLEAN/DAM_OLD/
DAM/LOW`, `PLAYERCAR2CLEAN/DAM/LOW`, `PONTIAC*/TORINO*/PICKUP*`. `lev1.py`
groups them by base name into the eight cars the bake consumes. The wheels are
the same named slots (`CLEANWHEEL` 4, `FASTWHEEL` 6, `HUBCAP` 77, `DAMWHEEL`
78), so the bake carries the beta's own wheel models and art.

The texture pages are not the retail `TEXTUREINFO` + separate region: the
`TEXTURES` lump starts with `[tpage_amount][field4]` and then a table of 8-byte
`{flags, offset}` entries. Page **set s** begins at `TEXTURES_body + 8 +
TP[s].offset`; its size is the next entry's offset. `pages1.py` returns those
as page entries, and `read_page`/`decomp_asm` decode them unchanged.

**The beta's poly texture ids are 16-bit TEXINF ids, not retail CLUT rows.**
A beta poly stores e.g. `5004` in the two bytes where retail stores a row
`0..31`. `euro43.map_model_ids()` looks that id up in its page's TEXINF array
and rewrites the poly to the record's index (the CLUT row the engine expects),
including ids shared on another page and the low-detail `0x100` flag. Without
that translation every car drew the page's last CLUT row and looked flat.

Bake and register it exactly like a retail city:

```bash
T=JERICHO/MODS/d1cars/tools
D=src_rebuild/bin/Release_dev
python $T/bake.py --levels $D/DRIVER/LEVELS --city BETAMIAMI --out $D/DRIVER/D1CARS
python $T/cosmetics1.py --levels $D/DRIVER/LEVELS --donor $D/DRIVER2/LEVELS \
       --cities BETAMIAMI --write $D/DRIVER/D1CARS
python $T/add_car_sounds.py --lev $D/DRIVER/D1CARS/BETAMIAMI.LEV \
       --voices $D/EURO43/SOUND/SOUND.SBK
python $T/textured.py --baked $D/DRIVER/D1CARS/BETAMIAMI.LEV --wheeled
```

`d1pool.CITY_SOURCE_FILE` maps `BETAMIAMI` to
`../../EURO43/LEVELS/BETAMIAMI`, so the tools read it from beside `DRIVER/`.
Its sound bank is `EURO43/SOUND/SOUND.SBK`, a bare count + 16-byte
`(address, length, loop, rate)` directory + VAG data. The beta bank's sample 1
(2,112 bytes, 11025 Hz) is the acceleration engine and sample 0 (3,200 bytes,
11025 Hz) the idle; the horns are the retail `VOICES.BLK` samples 6/7/8, mixed
into the same composed bank. The retail bank remains the fallback.

**Siren wiring.** Retail cop cars at source slot 1 (MIAMI, FRISCO, LA, NEWYORK)
and source slot 8 (LA, IVIEW) use retail `VOICES.BLK` sample 4; the beta's car
model 3 uses `SOUND.SBK` sample 7. The imported city has ONE generic composed
bank, so `add_car_sounds.py --city` replaces that bank's third (horn) sample with
the siren: sample 4 for MIAMI/FRISCO/LA/NEWYORK/IVIEW, sample 7 for BETAMIAMI.
Every imported car from that city shares the generic bank, so the siren is the
city's generic horn. The engine's automatic `CarHasSiren` loop still uses the
global siren bank.

**Wheel-palette comparison.** The beta's set 1 has 27 CLUT rows and differs from
retail MIAMI's set 1 in one row; the beta's set 53 has 24 rows while retail
MIAMI's set 53 page has none. The beta wheel pages are not the retail set and
the engine's wheel draw path did not take them (clean, damaged and fast all
rendered wrong). BETAMIAMI therefore uses MIAMI's known-good CLEAN/DAM/FAST
wheel art (`SELF_WHEEL_CITIES` is empty), exactly like the other D1 cities; the
engine derives the wheel palette from the MIAMI wheel sprites (rows 6/6 of 27).

The bank is registered in `JERICHO/CONFIG/cities.ini`:

```ini
city_0 = BETAMIAMI, ..\DRIVER\D1CARS\BETAMIAMI.LEV, ..\DRIVER\D1CARS\BETAMIAMI.LCF
```

`city_0` is city index 11. It appears in the frontend car list and the mp
picker like the seven stock Driver 1 car-data cities; it is a car source, not a
playable map.

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
