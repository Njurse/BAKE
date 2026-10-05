# d1cars tools

The offline extraction and bake pipeline. **Not written yet** — this is the
Phase 2/3/4 work; see [`../docs/DRIVER1.md`](../docs/DRIVER1.md) for what each
step does and how it is verified.

Planned, in order:

| tool | phase | what it does |
|---|---|---|
| `lev1.py` | 2 | walk a Driver 1 `LEVELS/*.LEV`, decode the car-models lump and the model-names lump into a neutral model |
| `d1pool.py` | 3 | extract every civilian (`CIVCAR*`) shape from all eight `.LEV`s, dedupe into one catalogue, emit `.obj` + `geom.json` + `pool.json` |
| `bake.py` | 4 | bake the catalogue into an engine-shaped car-data blob (a synthetic fifth "city") |

Everything here is read-only against the Driver 1 disc data. Run it from the
repo; `tools/out/` is ignored.
