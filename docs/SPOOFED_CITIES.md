# Spoofed cities — adding Driver 1's four cities alongside Driver 2's

**Goal.** Make Driver 1's cities selectable *in addition to* the four Driver 2
cities — Miami, San Francisco (FRISCO), Los Angeles and New York — so a session
can hold eight cities, four of them D1. "Spoofed" because each D1 city is dressed
as a Driver 2 city row so the existing machinery accepts it.

This document is the plan for the **engine** half. Everything the offline half can
do is already done and is listed in §1. Read `DRIVER1.md` first: §3 has the format
facts, §4 what the engine consumes, §6 the `[4]` tables this plan has to grow, §8
the scale and the cosmetics.

---

## 1. What is already built (the offline half is done)

All of it lives in this submodule, all of it is a plain file artefact, none of it
touches the engine. Reproduce with `tools/` in the order given.

| artefact | produced by | what it is |
|---|---|---|
| `out/bake/D1POOL.LEV` | `bake.py` | the pool as a Driver 2 shaped car-model blob: 13 models, `LUMP_CAR_MODELS` + `LUMP_TEXTUREINFO` + `LUMP_PALLET`, and the texture pages themselves after DATA1 |
| `out/lcf/MIAMI.LCF`, `out/lcf/FRISCO.LCF`, `out/lcf/LA.LCF`, `out/lcf/NEWYORK.LCF` | `cosmetics1.py --write` | one per city, in the engine's exact format — 52-byte table, 13 × 236 bytes, padded to 4096 |
| `out/pool/pool.json` + `*_geom.json` | `d1pool.py` | the 43 deduped shapes with their geometry, texture naming and normals |
| `out/pages/<LEVEL>/` + `<LEVEL>.json` | `pages1.py --write` | every car texture page decoded, with its CLUT rows |
| `out/wheels/` | `wheels1.py` | the wheel models and the shared wheel textures |
| `out/wheels/placement.json` | `wheels1.py --place` | per-car wheel centres, radius, track, wheelbase |
| `out/preview*.png`, `textured*.png`, `spin*.gif` + `out/spin*/` | `preview.py`, `textured.py`, `spin.py` | the renders, and the wheeled extra copies |

**Verified, not assumed:**

- `pages1.py --selftest` — the page codec, no game data needed;
- `lev1.py <level> --verify` — `clean (0 problems)` on all 8 D1 levels;
- `cosmetics1.py --selftest` — the wheel arithmetic round-trips all 208 of
  Driver 2's own wheel entries;
- `cosmetics1.py --check` — every derived field inside Driver 2's own ranges;
- `cosmetics1.py --verify` — each `.LCF` is 4096 bytes with records ending at the
  3120 the engine reads;
- `wheels1.py --check` — one shared wheelbase, four wheels on every shape;
- `textured.py --baked` — 0 polys with no page.

The pipeline is green end to end: `pages1 --selftest` → `lev1 --verify` →
`d1pool` → `bake` → `preview` → `textured --baked` → `spin`.

---

## 2. The engine half: what has to change

### 2a. Tables that need a fifth (eighth) row

Every one of these is a `[4]` today. Each needs a row per new city — so `[4]` →
`[8]`, and each new row filled with the D1 city's name and file.

| what | where |
|---|---|
| `LevelNames[]`, `LevelFiles[]` | `system.c:130`, `system.c:137` |
| `CosmeticFiles[]` | `cosmetic.c:14` |
| `carTpages[4][8]`, `specTpages[4][12]` | `texture.c:72`, `texture.c:37` |
| `CarAvailability[4][10]`, `carNumLookup[4][10]`, `minmaxSelections[4][2]` | `Frontend/FEmain.c:632`, `:639`, `:646` |
| `bootLevelNames[4]` | `main.c:3099` |

### 2b. Hard limits — the real ceiling

These are not arrays; they are guards that will refuse a fifth city. Each has to
be raised with the array it guards.

| limit | where |
|---|---|
| `#define CHK_CITY_COUNT_LIMIT 4` | `MODS/carhacks/carid.h:25` |
| `#define CHK_CAR_CITY_COUNT 4` | `MODS/carhacks/carimport.h:92` |
| `static CAR_IMPORT gCarImports[4]` + `city >= 4` guards | `models.c:258`, `:356`, `:450`, `:463`, `:502` |
| `static int gCarModelSource[MAX_CAR_RESIDENT_MODELS]` guard | `mission.c:332` |
| `city >= CHK_CAR_CITY_COUNT` offer gate | `MODS/carhacks/carimport.c:477` |
| `level > 3`, `pickCity < 4`, `src < 4` | `MODS/carhacks/carhacks.c:92`, `:342`, `:346` |
| `city >= 4` and the `imported < 4` loop | `cars.c:3113`, `:3175`, `:3240` |

### 2c. Frozen dimensions in `exports.def`

`exports.def` names several of these arrays **with their dimensions**
(`carTpages[4][8]`, `specTpages[4][12]`, `carNumLookup[4][10]`,
`CarAvailability[4][10]`). Changing a header's dimension without the matching
`exports.def` edit breaks the link in every configuration. **They move in the same
commit** — see the note in `DRIVER1.md` §6.

### 2d. Where the cars live

The resident-slot pool is `MAX_CAR_RESIDENT_MODELS 12` (`Game/dr2limits.h:35`)
with `SPECIAL_CAR_SLOT 11` (`:40`). Slots **5..10** are already module spares
(`mission.c:357 SetupResidentModels`) and they are where pool items belong. That
is six usable spares against four new cities, so a full eight-city session cannot
hold every city's cars resident at once — see §3.

---

## 3. What this round readies, and what the engine still owns

**Readied here.** Every file the engine will ask for, in the shape it already
reads: the car pool blob, the four `.LCF`s, the pages, the wheels, the placements.
Nothing in §1 needs inventing during the engine session — it needs *wiring*.

**Still owned by the engine session**, in rough order of how much can go wrong:

1. **The `carTpages` slot count.** This is the one genuine unknown. `carTpages`
   is `[city][8]` — eight texture-page slots per city — while the pool names
   **19** sets across its 13 models. Driver 2 fits its cars into 8 because a city
   only needs the pages *its own* cars are textured from; the pool, drawn from
   seven D1 levels, needs more. Something has to decide what a pool set maps
   onto: grow the second dimension (and take the VRAM from somewhere), or reduce
   the pool's page count (see `DRIVER1.md` §7 and §9, where this is written up as
   an open question).
2. **The VRAM budget.** The texture area is fully committed — see
   `vram-layout-and-headroom` for the layout and `jericho-vram-clut-column` for
   the CLUT column, where rows 466..511 are the level font. A fifth and sixth city
   worth of pages will not fit without taking rows back from somewhere. Measure
   with `-vramview` and the `JERICHO-VRAM` line before committing to a layout.
3. **The city-name plumbing.** `LevelNames`/`LevelFiles` feed the frontend and the
   level loader; the D1 cities need entries that resolve to the D1 data. The
   spoofing trick is that a D1 city occupies a D2 *row*, so the engine never
   learns it is loading Driver 1 data.
4. **The car-select path.** `CarAvailability`/`carNumLookup` decide what the
   frontend offers. carhacks already owns the car list (`carhacks-car-list-ownership`);
   the new cities' rows have to be filled consistently with it, or the frontend
   and the importer will disagree about which cars exist.

**Not in scope, and deliberately:** damaged and low-detail variants beyond what
the blockout needs, the undocumented `DT*.BIN` tables (`DRIVER1.md` §5), and any
attempt to run Driver 1's own level geometry as a Driver 2 level. Only the cars
travel.

---

## 4. Order of work

1. **Grow the tables and the guards in one commit** (§2a, §2b, §2c together). Do
   not split them: a header change without `exports.def` does not link, and an
   array without its guard produces a silent refusal that looks like a data bug.
2. **Load one D1 city and do nothing else** — no car change, no roster. If the
   city list shows eight and a D1 city loads, the rows are right. Nothing else can
   be trusted before this.
3. **Make one D1 city's `.LCF` reach `ProcessCosmeticsLump`** (`cosmetic.c:71`),
   and confirm the wheels are where `wheelDisp` says. The `delta.vz` the engine
   applies is small by construction (`DRIVER1.md` §8), so a car that sits oddly is
   a placement bug, not a cosmetics one.
4. **One resident slot, one car.** Take slot 5, import a single pool item, and
   check geometry, palette and wheels before touching the roster.
5. **The roster row** — categorise the D1 cities separately from the four D2 ones
   (`DRIVER1.md` §9, Phase 6).
6. **The test rig** — extend carhacks' `import = slot:<src>:<model>` to
   `slot:pool:<pool_index>`, then headless screenshots plus `crosscheck.py` /
   `hostdiff.py` for the VRAM and font check.

---

## 5. How to tell it worked

- **City list:** eight rows, four of them D1, and the four D2 ones unchanged.
- **Load:** each D1 city loads without a dump. A rejection prints and exits before
  the frontend — see `boot-arg-rejection-looks-like-a-crash`, which is what that
  looks like when it is a bad argument rather than a crash.
- **Geometry:** the car is the right size band (`DRIVER1.md` §8's table) and
  faces the right way — D1 and D2 share a winding convention, so no reversal.
- **Cosmetics:** wheels under the arches, `delta.vz` small, and a
  `CARMODEL_<n>.COS` override still wins where one exists.
- **VRAM:** `-vramview` plus the `JERICHO-VRAM` line, compared against the
  pre-change numbers, with the font rows (466..511) intact.
- **Regression:** the four D2 cities still load and still render their own cars.
  This is the check that gets skipped and matters most.

---

## 6. Cross-references

- `DRIVER1.md` §3 — the D1 car model, page, palette and normal formats, measured;
- `DRIVER1.md` §4 — what the engine consumes, and why the bake targets it;
- `DRIVER1.md` §6 — every `[4]` and every guard, with file:line;
- `DRIVER1.md` §7 — constraints and open questions, including the `carTpages`
  slot count;
- `DRIVER1.md` §8 — the scale factor and the cosmetics format;
- `DRIVER1.md` §9 — the roadmap and the pick-up checklist;
- `tools/README.md` — what every tool does and how to run it;
- carhacks' own docs (`MODS/carhacks/HANDOFF.md`) — the import path this rides.
