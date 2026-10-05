# d1cars tools

The offline pipeline: read the Driver 1 disc, extract the traffic shapes, bake
them into an engine-shaped blob. All read-only against the game data, run from
the repo root. `tools/out/` is ignored.

| tool | what it does |
|---|---|
| `lev1.py` | reads a Driver 1 `LEVELS/*.LEV` — `--lumps` the section table, `--names` the level-model names, `--models` every car-model variant, `--verify` its invariants |
| `scalemodels.py` | measures D1 vs D2 car-model sizes; `--plan` prints the per-item plan (D1 bbox -> scaled bbox -> nearest D2 car) |
| `d1pool.py` | extracts and dedupes the traffic shapes into `out/pool/*.obj` + `*_geom.json` + `out/pool.json`; `--render` a wireframe sheet |
| `bake.py` | bakes pool items into an engine-shaped car-data blob (`out/bake/D1POOL.LEV`) |
| `preview.py` | shaded 3/4 renders of the pool and of a baked blob -> `out/preview_*.png` |
| `textured.py` | textured 3/4 renders from the level's own pages -> `out/textured_pool.png` |

Typical run (both level directories are gitignored local data):

```sh
LEV=src_rebuild/bin/Release_dev/DRIVER/LEVELS     # Driver 1
D2=src_rebuild/bin/Release_dev/DRIVER2/LEVELS     # Driver 2

python JERICHO/MODS/d1cars/tools/lev1.py $LEV/*.LEV --verify
python JERICHO/MODS/d1cars/tools/scalemodels.py --d1 $LEV --d2 $D2 --plan
python JERICHO/MODS/d1cars/tools/d1pool.py --levels $LEV --render
python JERICHO/MODS/d1cars/tools/bake.py --pool JERICHO/MODS/d1cars/tools/out/pool.json --first 13
python JERICHO/MODS/d1cars/tools/preview.py
```

Current results: 43 unique clean shapes across the seven car-bearing levels;
scale 2048/3000; `bake.py --first 13` fills the 13-model car-data table.

See [`../docs/DRIVER1.md`](../docs/DRIVER1.md) for the format work and the
roadmap, and [`../docs`](../docs) for the phase status.
