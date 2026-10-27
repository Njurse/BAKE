# BAKE toolkit

BAKE is the asset-authoring suite that feeds the core modules:

| suite | folder | feeds |
| --- | --- | --- |
| car | `car/` | the Driver 1 car-data banks used by carhacks |
| ped | `ped/` | the JMPPED pedestrian archive used by pedhacks |
| common | `common/` | helpers shared by both suites |

Generated workspaces (`out/`, `.state/`, `__pycache__/`) are local artifacts and
must not enter the runtime mirror or a store package.
