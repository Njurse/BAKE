# BAKE Toolkit — Driver 1 (1999) traffic car shapes as a fifth car-data source

A JERICHO module that lifts the civilian traffic car **shapes** out of the
original Driver 1 PSX level files and offers them as a car-data source that sits
**alongside** the four Driver 2 cities (Chicago / Havana / Rio / Vegas), not in
place of one.

> **Status: SCAFFOLD (Phase 1).** The module builds and registers, but has no
> behaviour yet — no config keys, no hooks, no loader. The design, the measured
> format comparison and the phase roadmap are in **[`docs/DRIVER1.md`](docs/DRIVER1.md)**.

## Why this can exist

`carhacks` already knows how to import a car out of *another city's* `.LEV`:
`LoadCarImport` (`src_rebuild/Game/C/models.c`) reads a foreign `DATA1` region and
pulls out the car-models lump (id 28), the palettes (id 25) and the texture-page
info (id 34). The engine's car geometry is a plain `PolySizes`-walkable primitive
stream (`cars.c`), and Driver 1's is a *simpler, uncompressed* relative of it — so
an offline converter can emit a blob in exactly the shape that path already eats,
and the whole import machinery is reused.

The interesting part is not the file I/O; it is the bucket it lands in. "Four
cities" is baked into a row of data tables and `[4]` arrays all over the engine,
so the fifth source is a structural change, not a config toggle. `docs/DRIVER1.md`
enumerates every one.

## Layout

| path | what |
|---|---|
| `bake.c` / `bake.h` | the unit — one entry, `bake_register(ctx)` |
| `bake_module.c` | the module wrapper — `jer_module_bake_entry` |
| `mod.toml` | module metadata (compiled-in / deep mod, off by default) |
| `docs/DRIVER1.md` | the format work and the roadmap |
| `tools/car/` | the offline car extractor + bake |
| `tools/ped/` | the JMPPED pedestrian toolkit |
| `tools/common/` | shared helpers |

## Building

Auto-discovered: any `JERICHO/MODS/<id>/` with a `mod.toml` is a module, the
folder name is the id and the C entry symbol. No list file governs the build.
Enable it at runtime in `JERICHO/CONFIG/modlist.ini` (`bake = 1`); it ships
`0`/off and is deliberately **not** fetched by CI (it is a private submodule —
see `.github/workflows/build.yml`, same treatment as `gaildrv2`).
