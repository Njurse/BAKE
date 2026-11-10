# BAKE toolkit

BAKE is the asset-authoring suite that feeds the core modules.

The four pipelines stay separate. These are the public commands; the script
names in the table are the implementation and still run.

| command | what | same as |
| --- | --- | --- |
| `python tools/car/pool-bake.py` | Driver 1 pool | `d1pool.py` to extract (`--levels`), `bake.py` to bake (`--pool` or `--city`) |
| `python tools/car/d2-export.py` | Driver 2 export and rebuild | `exportd2.py` (`--rebuild-bank`); `make_new_bank.py` when you pass `--model` / `--slot` / `--lev` |
| `python tools/car/bank-bake.py` | declared bank bake | `carbank_bake.py` |
| `python tools/ped/ped-bake.py` | JMPPED pedestrians | `jmpped.py` (`ped-bake bake` is the archive bake) |

## Suites

| suite | folder | feeds |
| --- | --- | --- |
| car | `car/` | the Driver 1 car-data banks used by carhacks |
| ped | `ped/` | the JMPPED pedestrian archive used by pedhacks |
| common | `common/` | helpers shared by both suites |

Generated workspaces (`out/`, `.state/`, `__pycache__/`) are local artifacts and
must not enter the runtime mirror or a store package.
