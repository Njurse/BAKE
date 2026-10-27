# JMPPED authoring workspace — naming contract

This is the naming contract for the pedestrian authoring workspace under
`JERICHO/MODS/mp/tools/`. It replaces the old `out/jmppeds/v3` vocabulary.
Existing workspaces are moved once by `JMPPED.bat migrate`; normal commands do
not write the old layout.

## Layout

```text
JERICHO/MODS/mp/tools/
  project.json                     workspace manifest (was jmpped.json)
  characters/                      imported/authored characters
    <slug>/
      character.json               was ped.json
      model.obj                    the one editable model (was bindpose_edit.obj)
      models/blender/<part>.obj    internal Blender-space cache (was obj/edit)
      models/engine/<part>.obj     internal engine-space cache (was obj/raw)
      models/engine_reference.obj  generated reference (was bindpose_raw.obj)
      models/reference/<part>.obj  reference-only groups such as HIPS (never baked)
      textures/pages/              was png/pages
      textures/palettes/           was png/palettes
      textures/crops/              was png/parts
  out/                             exports and generated builds only
    source/<level>/                extracted Driver 2 level (was source/current)
      base.LEV
      base_v5.LEV
      skeleton/
      source_models/{tanner,jericho}/
    build/
      JMPPED.LEV
      preview.gif
  .state/                          last-used paths and backups (not an export)
```

`out/` means **export or generated build**. Authored and imported characters
live beside it in `characters/`, never under `out/`. A character folder is the
unit of import: one folder, one character.

## User-facing actions

The single CLI, `jmpped.py`, exposes exactly these verbs:

| Verb | Input | Output |
| --- | --- | --- |
| `extract` | a Driver 2 level | `out/source/<level>/` |
| `import` | `characters/<slug>/model.obj` | `models/{blender,engine}/` |
| `bake` | `project.json`, `characters/`, `out/source/` | `out/build/JMPPED.LEV` |
| `preview` | `out/build/JMPPED.LEV` | `out/build/preview.gif` |
| `add-character` | an existing character | a new `characters/<slug>/` and a texture-page allocation |
| `sync-source` | `out/source/<level>/` | refreshed source-owned assets in the project |
| `migrate` | an old `out/jmppeds/` workspace | the new project layout |
| `status` | `project.json` | the project and source paths, and readiness |
| `rebuild-all` | a level and a project | extract, import, bake and preview in order |

`split` is an implementation detail of `import`. `register` is
`import --folder`. `build` is not a user verb: the game is built with
`JERICHO/build.py`, while this toolkit **bakes** the archive.

## Folder and file renames

| Old | New | Meaning |
| --- | --- | --- |
| `out/jmppeds/` | `out/` + `characters/` + `.state/` | the plural container is gone |
| `out/jmppeds/v3/` | workspace root (`tools/`) | `v3` is an archive format, not a workspace |
| `out/jmppeds/source/current/` | `out/source/<level>/` | source export named by level |
| `out/jmppeds/backups/` | `.state/backups/` | generated state |
| `out/jmppeds/last_workspace.txt` | `.state/last_project.txt` | selected project |
| `out/jmppeds/last_source.txt` | `.state/last_source.txt` | selected source export |
| `entries/<slug>/` | `characters/<slug>/` | one selectable character |
| `entries/<slug>/ped.json` | `characters/<slug>/character.json` | per-character manifest |
| `obj/edit/` | `models/blender/` | internal Blender-space parts |
| `obj/raw/` | `models/engine/` | internal engine-space parts |
| `obj/bindpose_edit.obj` | `model.obj` | the one editable model |
| `obj/bindpose_raw.obj` | `models/engine_reference.obj` | generated reference, never an input |
| `obj/assembled_*.obj` | removed | the preview renders the archive directly |
| `png/pages/` | `textures/pages/` | indexed texture pages |
| `png/palettes/` | `textures/palettes/` | palette strips |
| `png/parts/` | `textures/crops/` | per-material RGB crops |
| `jmpped.json` (workspace) | `project.json` | workspace manifest |
| `base.LEV`, `base_v5.LEV` | `out/source/<level>/base.LEV`, `base_v5.LEV` | source exports |
| `tanner/`, `jericho/` (source subexports) | `out/source/<level>/source_models/{tanner,jericho}/` | extracted source models |
| `skeleton/` (workspace) | `out/source/<level>/skeleton/` | shared skeleton export |
| `jmpped/assembled_*.obj` | removed | visual reference |
| `preview_360.gif` | `out/build/preview.gif` | preview build output |

## Manifest keys

| Old | New |
| --- | --- |
| `manifest_version` | `project_version` |
| `source_level` | `source.level` |
| `source_sha256` | `source.sha256` |
| `base_archive` | `source.base` |
| `base_archive_sha256` | `source.base_sha256` |
| `base_v5_archive` | `source.base_v5` |
| `base_v5_archive_sha256` | `source.base_v5_sha256` |
| `source_workspace` | `source.project` |
| `archive_version` | `format_version` |
| `peds` | `source_models` |
| `entries` | `characters` |
| `directory` | `folder` |
| `source_ped` | `source_character` |
| `variant` | `skeleton` |
| `slots` | `parts` (each part keeps `slot` as the archive index) |
| `bindpose` | `model` (`path`, `sha256`) plus `engine_reference` |
| `page_sets` | `texture_page_sets` |
| `page_set_map` | `texture_page_map` |
| `pages[].set` | `pages[].page_set` |
| `palette_crops` | `texture_crops` |
| `display_clut` | `palette_row` |
| `npalettes` | `palette_rows` |
| `cluts_hex` | `palette_rows_hex` |
| `bake.slots` | `build.parts` |

Unchanged keys: `name`, `index`, `slot`, `part`, `texture`, `rect`, `rgb`,
`offset`, `scale`, `materials`, `crops`, `pages`.

## Actions and scripts

| Old | New | What it does |
| --- | --- | --- |
| `export` | `extract` | Driver 2 level → `out/source/<level>/` |
| `split` | `import` | one model → 14 internal parts; not a separate user step |
| `build` | `bake` | workspace → `out/build/JMPPED.LEV` |
| `prime` | `sync-source` | refresh source-owned assets into the project |
| `add-entry` | `add-character` | clone a character and allocate its texture pages |
| `register` | `import --folder` | import a character folder |
| `preview` | `preview` | render the baked archive |
| `all` | `rebuild-all` | extract, import, bake and preview |
| `status` | `status` | show the project and source paths |
| — | `migrate` | one-time move from the old layout |
| `BAKE_JMPPED.bat/sh` | `JMPPED.bat/sh` | thin launcher over `jmpped.py` |
| `JMPPED_WIZARD.bat` | `CHARACTER_WIZARD.bat` | console menu |
| `REGISTER_JMPPED.bat/sh` | `IMPORT_CHARACTER.bat` / `import_character.sh` | drag-and-drop import |
| `jmpped_bake.py` (wrapper) | `jmpped.py` | the single CLI |
| `jmpped_build.py` + `jmpped_v5.py` | `jmpped_bake.py` | archive builder |
| `jmpped_export.py` | `jmpped_extract.py` | source extraction |
| `jmpped_split.py` + `jmpped_register.py` | `jmpped_import.py` | model and folder import |
| `jmpped_add_entry.py` | `jmpped_add_character.py` | add a character |
| `jmpped_prime.py` | `jmpped_sync_source.py` | source sync |
| `jmpped_crops.py` | `jmpped_textures.py` | crop splitting |
| `jmpped_source.py` | `jmpped_level.py` | level lump reader |
| `bake_jmpped.py` | `extract_level_peds.py` | level → base archive |
| `jmpped_common.py` | `jmpped_core.py` + `jmpped_archive.py` + `jmpped_models.py` + `jmpped_images.py` | split by intent |
| `docs/JMPPED.md` | `docs/PEDESTRIAN-AUTHORING.md` | this workspace's guide |

## Unchanged engine-owned names

The rename stops at the authoring workspace. These names are part of the game
and its archive format and do not change:

| Name | Where it lives | Why it stays |
| --- | --- | --- |
| `JMPPED.LEV` | `DRIVER2/LEVELS/JMPPED.LEV` and `src_rebuild/bin/<config>/DRIVER2/LEVELS/` | the engine's fixed archive path |
| **entry** | `JerPedEntryCount()`, `JerPedEntryName()`, `JerPedModelForEntry()`, `JerPedImportBaked*()`, `PEDESTRIAN.pedEntry` | the C API and the engine's selectable-character index |
| **slot** | the 14 archive positions, `JerPedModelForEntry(entry, slot)` | the format's model index; authoring calls the geometry a **part** and uses `slot` only as that index |

Authoring prose uses **character** for the selectable unit, **part** for one of
the 14 models, and **slot** only when it means the archive index 0–13.

## Rules

- `JMPPED.LEV`, the engine's `JerPed*` API and its word **entry** keep their
  names. Authoring prose uses **character** for the selectable unit and **part**
  for one of the 14 models; **slot** is only the archive index 0–13.
- `model.obj` is the one editable and importable model. `import` and `bake`
  split a changed combined model automatically; the per-part OBJs under
  `models/{blender,engine}` are generated caches, and reference-only groups are
  written under `models/reference/`.
- Every `*_sha256` value is a change detector, not a checksum of what is on
  disk. Migration moves the path and preserves the recorded hash, so a pending
  `model.obj` edit is still seen as pending after the move.
- `out/` never holds authored state. Delete `out/build/` and the next bake
  recreates it; delete `characters/` and the character is gone.
- No permanent aliases: old names are read only by `migrate`.
