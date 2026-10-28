# BAKE car tools

The offline pipeline: read the Driver 1 disc, extract the traffic shapes, bake
them into an engine-shaped blob, and preview the result. Everything is read-only
against the game data and runs from the repo root. `tools/car/out/` is gitignored.

| tool | what it does |
|---|---|
| `lev1.py` | reads a Driver 1 `LEVELS/*.LEV` — `--lumps` the section table, `--names` the level-model names, `--models` every car-model variant, `--verify` its invariants |
| `pages1.py` | the texture half of the same file: `--selftest` the RLE, `--info` the permanent page list, `--pallet` the car palette records, `--compare DIR` a structural cross-check against DriverLevelTool's TGAs, `--write DIR` PNGs + a JSON index |
| `scalemodels.py` | measures D1 vs D2 car-model sizes; `--plan` prints the per-item plan (D1 bbox -> scaled bbox -> nearest D2 car) |
| `d1pool.py` | extracts and dedupes the traffic shapes into `out/pool/*.obj` + `*_geom.json` + `out/pool.json`, carrying each poly's texture naming and both normal tables; `--render` a wireframe sheet |
| `bake.py` | bakes pool items into an engine-shaped blob (`out/bake/D1POOL.LEV`): car models, `LUMP_CAR_WHEELS` (clean/damaged/fast wheel models), `LUMP_PALLET`, `LUMP_TEXTUREINFO` and the texture pages. Self-checks by re-reading what it wrote |
| `exportd2.py` | the **Driver 2** car suite: exports every clean/damaged/low model as OBJ + geom JSON, the car-only texture pages/CLUTs/palettes and TEXINF details, the `.LCF` cosmetics, exact raw blocks, and a carhacks-loadable `<CITY>.LEV` + `.LCF` whose slot order comes from an editable `bank_order` |
| `basebank.py` | scaffolds a **one-car** Driver 2 base bank: a loadable `NAME.LEV` + `NAME.LCF`, that car's raw clean/damaged/low blocks, page entries/PNGs, palette rows and cosmetics, a `cities.ini` snippet, a README and a manifest that `exportd2 --rebuild-bank` accepts |
| `preview.py` | shaded 3/4 renders of the pool and of a baked blob -> `out/preview_*.png` |
| `textured.py` | textured 3/4 renders, lit, no wireframe by default. `--baked FILE` renders a baked blob **from its own embedded pages** (`--pages-root` + tool TGAs is the older pool path) |
| `spin.py` | rotating textured+lit GIFs of a baked blob -> `out/spin_baked.gif` (all shapes in one grid) and `out/spin/<n>_model<n>.gif` |
| `driveby.py` | live window: one pool car with spinning wheels, 3 s of the beta idle then six gears of the Euro-43 engine loop at `--engine-pitch` / `--idle-pitch`; Space replays (winsound) |
| `wheels1.py` | the level's wheel models and the shared wheel textures, to the side (`--verify`); `--place` writes the per-car placement record; `--check` pins the placement invariants |
| `wheelgeom.py` | where a wheel goes: the donor derivation and its measured bands (`--donor`, from Driver 2's own `.LCF`s), and `placement_from_donor()` — the per-car wheelbase, track and ride height |
| `wheelrender.py` | the shared "draw a wheel" used by all three renderers — the wheel model, its sprite texture and its transparency, replicated at each centre and optionally spun about its axle |
| `cosmetics1.py` | a full 236-byte `CAR_COSMETICS` per car: `--selftest` the engine's wheel arithmetic, `--build` derive and print, `--check` against Driver 2's own ranges, `--write DIR` the four city `.LCF`s, `--verify DIR`, `--dump FILE` |

The wheeled renders are **extra copies** — the plain sheets are left untouched
(verified byte-identical):

```sh
python $T/preview.py   --wheeled      # + preview_wheeled.png, preview_pool_wheeled.png
python $T/textured.py  --baked $T/out/bake/D1POOL.LEV --wheeled
python $T/spin.py      --wheeled      # + spin_wheeled.gif, spin_wheeled/*.gif
```

`--wheel-turns N` sets how many revolutions the wheels make per turn of the car
(default 3), which is what makes the tyre texture visibly rotate.

Typical run (both level directories are gitignored local data):

```sh
LEV=src_rebuild/bin/Release_dev/DRIVER/LEVELS     # Driver 1
D2=src_rebuild/bin/Release_dev/DRIVER2/LEVELS     # Driver 2
T=JERICHO/MODS/d1cars/tools

python $T/lev1.py $LEV/*.LEV --verify
python $T/pages1.py $LEV/FRISCO.LEV --info
python $T/pages1.py $LEV/FRISCO.LEV --pallet
python $T/pages1.py $LEV/FRISCO.LEV --write $T/out/pages
python $T/scalemodels.py --d1 $LEV --d2 $D2 --plan
python $T/d1pool.py --levels $LEV --render
python $T/bake.py --pool $T/out/pool.json --first 13
python $T/wheels1.py --place && python $T/wheels1.py --check
python $T/preview.py --wheeled
python $T/textured.py --baked $T/out/bake/D1POOL.LEV --wheeled
python $T/spin.py --wheeled
python $T/cosmetics1.py --selftest
python $T/cosmetics1.py --check
python $T/cosmetics1.py --write $T/out/lcf     # writes and then re-reads each file
```

## Driver 2 cars: `exportd2.py`

`exportd2.py` is the D2-only counterpart to the D1 bake. It reads one
`DRIVER2/LEVELS/<CITY>.LEV` (and the sibling `<CITY>.LCF`) and writes an
editable, self-contained package plus a carhacks-loadable bank. Driver 1 stays
with `bake.py` / `d1pool.py`.

```sh
D2=src_rebuild/bin/Release_dev/DRIVER2/LEVELS
T=JERICHO/MODS/bake/tools/car

python $T/exportd2.py $D2/CHICAGO.LEV -o $T/out/exportd2
python $T/exportd2.py $D2 --all -o $T/out/exportd2
python $T/exportd2.py --selftest
```

Output per city:

```
out/exportd2/
  index.json                     master list, with each city's manifest
  CHICAGO/
    CHICAGO.export.json          cars, variants, sets, raw paths, bank_order
    CHICAGO.cosmetics.json       named CAR_COSMETICS fields + raw records
    CHICAGO.LCF                  source cosmetics copy
    CHICAGO.textures.json        pages, CLUT rows, details, LUMP_PALLET
    CHICAGO_palettes.png         palette swatch sheet
    models/                      <car>_<variant>.obj + .geom.json + .mtl
    textures/                    PAGE_<set>.png for car/wheel sets
    raw/                         exact model blocks, pages and lump bodies
    bank/                        CHICAGO.LEV + CHICAGO.LCF + bank.json
```

Only the texture sets the exported cars name, plus the sets carrying
`WHLCLEAN` / `WHLBOTH` / `WHEEL` details, are included. The raw blocks make
the package independent of the source `.LEV`; the bank is assembled from them.

To reorder the cars in the generated bank, edit `bank_order` in
`CHICAGO.export.json` (or pass a separate file) and rebuild:

```sh
python $T/exportd2.py --rebuild-bank $T/out/exportd2/CHICAGO/CHICAGO.export.json \
    --order my_order.json -o $T/out/exportd2/CHICAGO/bank
```

`bank_order` is a list of `{"slot": N, "car": "<CITY>_modelM"}` entries; a plain
ordered list of car IDs fills the D2-usable slots (0-4 then 8-12) in order. Slots
with no car stay `-1`, and the `.LCF` is rebuilt in the same slot order.
`--no-bank` stops after the source package. Generated output lives under the
gitignored `tools/car/out/`.

## One-car base banks: `basebank.py`

`basebank.py` is the smaller scaffold for a mod that only wants one car in a
bank, rather than a whole city's roster:

```sh
T=JERICHO/MODS/bake/tools/car
python $T/basebank.py --city CHICAGO --model 1 --name FIREBIRD \
    --slot 1 --out JERICHO/MODS/betacars/banks/firebird
```

It copies one donor car's three variants and only the texture sets that car
names, then writes:

```
firebird/
  FIREBIRD.LEV / FIREBIRD.LCF   the loadable one-car bank
  FIREBIRD.json                 manifest + bank report
  cities.ini                    the [cities] line to paste into CONFIG
  README.txt                    install/import instructions
  raw/models/                   model1_clean/damaged/low.bin
  raw/pages/                    PAGE_<set>.bin and .png for the car's sets
  raw/pallet.bin                source palette lump
  raw/palette.png / .json       resolved colours for those sets
  raw/source.LCF                source cosmetics
  raw/cosmetics/model1.cos      donor 236-byte record
```

Only the destination slot is populated; every other model slot is `-1`. The
default slot is **1** because carhacks treats model 0 as "no car". The
`cities.ini` snippet uses `city_0` by default, which the engine exposes as city
index **11**; use `--city-index N` for another bank row.

Rebuild after editing the raw blocks with the normal exportd2 path:

```sh
python $T/exportd2.py --rebuild-bank firebird/FIREBIRD.json -o firebird
```

`pages1.py --selftest` pins the RLE both ways and needs no game data at all, so
it is the cheapest thing to run after a change to the page decoder.

## Current results

* **43** unique clean shapes across the seven car-bearing levels (they share
  none), from `--first 13` baked into the 13-model car-data table.
* Scale `2048/3000`; every level's palette lump is an exact `4 + n*44` fit.
* `bake.py` writes 13/13 models with 0 poly errors and **2161 GT polys**, every
  normal index inside its table, and 19 texture sets (9 permanent + 10 special)
  whose pages it carries itself.
* Each per-city bake also writes a `LUMP_CAR_WHEELS` segment: the city's own
  `CLEANWHEEL` / `DAMWHEEL` / `FASTWHEEL` as FT4 models, with their texture
  pages added to the same page plan. A missing or display-scale variant falls
  back to the canonical car-scale D1 wheel (`wheelgeom.canonical_wheel`), so
  MIAMI's absent `DAMWHEEL` and NEWCASTLE's CREDITS source still carry D1
  wheels. The engine keeps the host wheel mesh and applies those textures (and
  their palettes) to it, rather than drawing the imported mesh.
* `textured.py --baked` renders those with **0 polys missing a page**.
* **43** cars each placed from the matched Driver 2 car: wheelbase 341..457
  (1.04..1.14 of each body's length, inside Driver 2's 1.04..1.28), a track per
  car, and a ride height where the body reaches 0.63..1.21 tyre radii past the
  contact — the same shape Driver 2's own cars have.
* **29** `CAR_COSMETICS` records over the four D1 cities, every field inside
  Driver 2's own ranges, and the wheel arithmetic round-trips all **208** of
  Driver 2's own wheel entries.
* Four `.LCF` files, each byte-structurally identical to a Driver 2 one.

A **headerless beta stream** (the EURO-43 `EURO43/LEVELS/MIAMI.LEV`) is read natively:
`lev1.py` walks its flat lump list, groups the named car slots into eight cars,
and `pages1.py` addresses its texture pages from the `TEXTURES` lump's TP table.
`bake.py --city BETAMIAMI` produces the same car-data shape as a retail city
(8 of 13 slots, its own wheels, 10 page sets). See
[`../docs/DRIVER1.md`](../docs/DRIVER1.md) §11 for the layout and the bank
registration.

## The two things that bite

* **Set numbers are per-level.** The same number means a different page in
  another level, so the bake renumbers each distinct (source level, set) and
  rewrites the polys. Skipping this makes two cities' cars share a page.
* **A level's car pages are usually the SPECIAL ones**, not the permanent ones —
  on FRISCO the car polys paint with sets 3, 4 and 61..66, all special; only
  67/68 are permanent. Both lists must be resolved, and they are laid out one
  after the other.

See [`../docs/DRIVER1.md`](../docs/DRIVER1.md) for the format work and the
roadmap, [`../docs/SPOOFED_CITIES.md`](../docs/SPOOFED_CITIES.md) for the plan to
add the four D1 cities alongside Driver 2's four, and [`../docs`](../docs) for the
phase status.
