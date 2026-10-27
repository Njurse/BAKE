# JMPPED — how the pedestrian archive is put together

This is the canonical explanation of a `JMPPED.LEV` bake: where its models,
pages and skeleton come from, what the builder writes, and how the engine reads
it. The toolkit quick-start stays in
[`../tools/README.md`](../tools/README.md); this page is the full assembly map.

> **Runtime ownership (interface 3).** The core order is
> `carhacks -> bake -> pedhacks -> mp`. `pedhacks` owns the archive lifecycle
> and the `jer_ped_archive.h` contract; `mp` consumes it and falls back to
> Tanner when the archive is unavailable. The engine v3/v5 parser and the
> authoring tools below remain engine/mp-owned for now; moving them is a
> separate change.

## 0. Workspace naming

The authoring workspace uses plain-language names. The full contract, including
the target layout, is in [`../tools/NAMING.md`](../tools/NAMING.md). These are
the renames that matter while reading the rest of this page:

| Old | New |
| --- | --- |
| `the project root` workspace | `tools/project.json` + `characters/` + `out/` |
| `characters/<slug>/` | `characters/<slug>/` |
| `character.json` | `character.json` |
| `models/blender` / `models/engine` | `models/blender` / `models/engine` |
| `model.obj` / `bindpose_raw.obj` | `model.obj` / `models/engine_reference.obj` |
| `textures/pages`, `textures/palettes`, `textures/crops` | `textures/pages`, `textures/palettes`, `textures/crops` |
| `project.json` | `project.json` |
| `peds` | `source_models` |
| `slots` | `parts` (`slot` stays the archive index) |
| `bindpose` | `model` |
| `skeleton` | `skeleton` |
| `source_ped` | `source_character` |
| `directory` | `folder` |
| `page_sets` / `palette_crops` | `texture_page_sets` / `texture_crops` |
| `display_clut` | `palette_row` |
| `archive_version` | `format_version` |
| `export` / `split` / `build` / `sync-source` / `register` | `extract` / `import` / `bake` / `sync-source` / `import --folder` |

`out/` is reserved for exports and generated builds; authored characters live
in `characters/` beside it, never under `out/`.

The user-facing verbs are `extract`, `import`, `bake`, `preview`,
`add-character`, `sync-source`, `migrate` and `status`. `split` is an internal
step of `import`, `register` is `import --folder`, and `build` is reserved for
building the game; this toolkit bakes the archive.

Engine-owned names do not change: the archive path stays `JMPPED.LEV`, the C
API keeps its word **entry** (`JerPedEntryCount()`, `JerPedEntryName()`,
`JerPedModelForEntry()`, `PEDESTRIAN.pedEntry`), and **slot** remains the
0–13 archive index. Authoring prose calls the selectable unit a **character**
and one of its 14 models a **part**.

## 1. What a JMPPED is

`JMPPED.LEV` is a self-contained pedestrian archive loaded from
`DRIVER2/LEVELS/JMPPED.LEV` by `JerPedImportBaked()`. It is engine-owned: the
`mp` module has no asset dependency (`JERICHO/MODS/mp/mod.toml` has
`dependencies = []`) and does not ship its own copy. `JERICHO/build.py` copies
the checked-in `JERICHO/MODS/mp/tools/JMPPED.LEV` beside the built game.

Two archive versions matter:

| Version | Shape | Use |
| --- | --- | --- |
| **3** | magic `JMPPED\0\0`, version, 14 model blobs, page records | the original single 14-slot character; still read by the engine and used as `base.LEV` |
| **5** | magic, version, entry count, then per entry: name, 14 model blobs, page records | multi-entry archive; up to `JMPPED_MAX_ENTRIES` / `JER_PED_MAX_ENTRIES` = **8** entries |

Version 4 is a legacy read-only shape (the old civilian-head experiment). The
current toolkit reads it but writes v3 or v5 only.

The engine's character id mapping is:

- `0` — Tanner, the engine's resident models (`pmTannerModels`)
- `1` — archive entry 0 (the Jericho set)
- `2..` — archive entries 1.. in the order they appear in the v5 archive

The mp layer (`MpPedModelName/Selectable/Available/Effective`) asks
`JerPedEntryCount()`/`JerPedEntryName()` and never owns the asset.

## 2. Source extraction

The source is a **Driver 2 level**, not a previous `JMPPED.LEV`. By default it is
`src_rebuild/bin/Release/DRIVER2/LEVELS/HAVANA.LEV`:

```
jmpped_extract.py
  └─ bake_jmpped.py --no-copy
       reads the level's model table, writes base.LEV (v3, 14 models, 3 pages)
  └─ writes tanner/, jericho/, skeleton/, jmpped/, project.json, base_v5.LEV
```

`jmpped_source.py` reads the level lumps:

- `LUMP_MODELNAMES` (12) — the model-name table
- `LUMP_MODELS` — the model blobs
- `LUMP_TEXTUREINFO` (34) — texture-page records
- `LUMP_SPOOLINFO` (26) — which pages belong to the Havana model areas
- `LUMP_MOTIONCAPTURE` (22) — the shared motion block used for the bind skeleton

The source split is:

- **Tanner**: the full `TORSO, HEAD, U_ARM_*, L_ARM_*, HAND_*, THIGH_*, CALF_*,
  FOOT_*, NECK, HIPS, BAG` set, pages `{2}`.
- **Jericho**: `JERI_TORSO, JERI_HEAD, JERI_U_ARM_*, JERI_L_ARM_*`, pages
  `{23, 25}`; the playable v3 Jericho also uses Tanner's limbs and hands.

The motion block supplies the 23-bone hierarchy. `skeleton.json` stores both the
motion-space positions (`motion_raw`, +Y up) and the engine draw positions
(`engine`, +Y down), plus `skeletons.tanner.bindings` and
`skeletons.jericho.bindings`.

## 3. The 14-slot map

Every entry has exactly these 14 model slots. Slots 0–5 are the Jericho upper
body; slots 6–13 are Tanner's limbs and hands. The bind bone column is the
Jericho/`OTHER_MODEL` binding; the Tanner skeleton binds `LTOE`/`RTOE` to the
opposite foot names, which is why `skeleton.json` keeps both skeletons.

| Slot | Part | Source ped | Bind bone | Entry-0 page set |
| ---: | --- | --- | --- | ---: |
| 0 | `JERI_TORSO` | Jericho | `JOINT_1` | 23 |
| 1 | `JERI_HEAD` | Jericho | `HEAD` | 25 |
| 2 | `JERI_U_ARM_LEFT` | Jericho | `LELBOW` | 23 |
| 3 | `JERI_L_ARM_LEFT` | Jericho | `LHAND` | 23 |
| 4 | `JERI_U_ARM_RIGHT` | Jericho | `RELBOW` | 23 |
| 5 | `JERI_L_ARM_RIGHT` | Jericho | `RHAND` | 23 |
| 6 | `THIGH_LEFT` | Tanner | `LKNEE` | 2 |
| 7 | `CALF_LEFT` | Tanner | `LFOOT` | 2 |
| 8 | `FOOT_LEFT` | Tanner | `LTOE` | 2 |
| 9 | `THIGH_RIGHT` | Tanner | `RKNEE` | 2 |
| 10 | `CALF_RIGHT` | Tanner | `RFOOT` | 2 |
| 11 | `FOOT_RIGHT` | Tanner | `RTOE` | 2 |
| 12 | `HAND_LEFT` | Tanner | `LFINGERS` | 2 |
| 13 | `HAND_RIGHT` | Tanner | `RFINGERS` | 2 |

The full Tanner torso/head/arms/neck/hips are exported for reference but have
`bake_slot: null`; v3/v5 cannot store them as separate slots.

## 4. Workspace and manifests

The default workspace is `tools/the project root` (gitignored):

```
base.LEV                 untouched v3 archive from the same level
base_v5.LEV              v5 seed with entry 0
project.json              the manifest: pages, entries, slots, skeleton, hashes
skeleton/skeleton.json   23 bones, motion_raw/engine positions, skeleton bindings
skeleton/skeleton_*.obj  raw/edit bone-edge views
tanner/...               full Tanner reference subexport
jericho/...              entry 0 parts, pages 23/25 and bindpose
characters/<slug>/...       later v5 entries (add-character output)
jmpped/assembled_*.obj   rough visual reference for the 14 slots
```

`project.json` (manifest version 2) is the builder's input:

- `source_level`, `source_sha256` — the HAVANA level used for the export
- `base_archive`, `base_v5_archive` and their SHA-256 values
- `archive_version` — what the builder writes
- `peds` — the source subexports (`tanner`, `jericho`)
- `entries` — the v5 entries. Each has `index`, `name`, `directory`,
  `source_ped`, `page_sets`, `pages`, `slots`, `bindpose`, `palette_crops`
- `slots` — the 14 descriptors: `slot`, `ped`, `part`, `page_sets`,
  `obj_raw`/`obj_edit` and hashes, `offset`/`scale`, `materials`, `crops`
- `pages` — page set, storage (raw/compressed), `display_clut`, page/crop paths
  and hashes
- `skeleton` — the hierarchy and skeleton bindings
- `bake.slots` — the authoritative slot→ped/part map

Each character also has a `character.json` mirror for humans and per-character
tooling. Hashes
are the change detector: a part is rebuilt when its selected OBJ copy, its page,
a crop, or its `offset`/`scale` differs from the exported value. A transform is
bake-time and applied to the vertices; it is not an engine runtime parameter.

## 5. Workflow

One character folder is the unit of import: it is what you edit and what you
hand back. The tool derives the 14 archive parts from that character.

```sh
JMPPED.bat extract --source DRIVER2\LEVELS\HAVANA.LEV
JMPPED.bat import --folder characters\jericho-driv3r
JMPPED.bat bake --copy
JMPPED.bat preview
```

1. **Extract.** `jmpped_extract.py` reads the Driver 2 level and installs
   `out/source/<level>/` (base archives, source models, skeleton). Custom
   characters are kept.
2. **Edit and bake.** A character folder is `characters/<slug>/` with
   `character.json`, `model.obj`, the generated
   `models/{blender,engine}/<part>.obj` caches and
   `textures/{pages,palettes,crops}/`. Edit the whole character in `model.obj`;
   `import` (for a new folder) or `bake` splits a changed model into the 14
   parts automatically. The per-part files are generated; the combined model
   wins.
3. **Bake.** `jmpped_bake.py` builds `out/build/JMPPED.LEV` and rereads it as a
   self-check. `--copy` writes `tools/JMPPED.LEV` and both runtime levels.
4. **Preview.** `jmpped_preview.py` renders `out/build/preview.gif` from the
   archive. It renders the last/custom character by default; pass
   `--character "Jericho Driv3r"` to choose one, or use
   `JMPPED.bat bake --copy --preview` to refresh the GIF in the same command.

`JMPPED.bat status` shows the project, characters and source. `add-character`
clones a character and allocates its page sets; `sync-source` refreshes the
source-owned first character from `out/source/<level>/`.

The old export, split, build, prime and register verbs are retired. `build` now means
building the game (`JERICHO/build.py`); this toolkit bakes the archive.

### Editing the combined model in Blender

`model.obj` is the whole character in its bind pose, in Blender Z-up at
1/1024 scale. Keep the 14 parts as separate objects or groups named exactly
after their parts (`JERI_TORSO`, `JERI_HEAD`, ... `HAND_RIGHT`); `HIPS` is an
optional reference. Blender's `o`/`g` groups and `PART_sN_tM` material
subgroups are both accepted. Do not join the parts into one mesh, and keep the
materials named `s<set>_t<id>`. Run `JMPPED.bat bake --copy`; the bake splits
the changed model, subtracts each part's bind offset and writes the caches. A
joined mesh, a renamed part or a missing part is refused with the part named.

## 5b. Textured model (Blender)

`model_textured.obj` is the canonical edit model. It has one object per part and per-part materials `PART__s<set>_t<id>`; each `map_Kd` points at `textures/crops/<part>_set<set>_t<id>.png`. UVs are 0..1 inside that crop. `model.obj` is generated page space for the splitter; do not edit it.

Open `characters/<slug>/model_textured.obj` in Blender, edit UVs and paint the crop PNGs in place, then run `JMPPED.bat bake --copy`. The splitter converts crop UVs back to page UVs and the baker rebuilds the page/CLUT. Run `JMPPED.bat textured --check --character "<name>"` to report every material, crop, rect, image size, palette row and UV bounds.

The wizard's **Extract a Driver 2 level** option is source-only: it refreshes `out/source/<level>` and the source template, not a custom character's `model_textured.obj`. After an extract, use **Rebuild the textured model** (wizard option 10) or `JMPPED.bat textured --character "<name>"`; then bake. Extraction refuses to replace a folder containing a different `character.json`, a `model_textured.obj`, or a `source.json`.

### Shared texture crops

`textured --combine` groups parts by the game texture (`page_set`, `texture_id`) and writes one crop image per group:

- page 26 `t9` (torso and both arm pairs) → `textures/crops/s26_t9.png` (16×64)
- page 3 `t12` (thighs, calves, feet) → `textures/crops/s3_t12.png` (49×64)
- page 3 `t2` (hands) → `textures/crops/s3_t2.png` (29×13)

The head (`t24`/`t25`) and glasses (`t22`/`t23`) are unique and stay per-part. Per-part material slots stay; every member slot points at the shared image. Wizard option 10 asks **Combine identical textures into shared crops? [y/N]** and defaults to N. `--combine` runs it, `--no-combine` keeps the old per-part output, and `--dry-run` reports the groups. `textured --check` and `status` list the shared crops and their members.

**If a texture is wrong, start here.** Check `model_textured.mtl` first: material `PART__s<set>_t<id>` must map to the right crop, crop size must equal its `rect`, and UVs must stay 0..1. Then check `character.json` crop rects and `texture_page_map`, and run `textured --check`.

## 6. Pages and CLUTs

A page is 256×256 at 4bpp. Each page carries a CLUT table with one 16-entry row
per texture id; the engine selects `civ_clut[0][texture_number][pal]`. The page
therefore cannot be represented by one palette row for every material.

- `page_<set>_indexed.png` — the page's indexed source. It is internal/base
  data, not the normal edit surface.
- `textures/palettes/page_<set>.png` — a strip of every CLUT row.
- `textures/crops/<part>_set<set>_t<id>.png` — the **single editable RGB crop** for
  that material. There is no `_indexed` twin; the builder paletteizes it.
- A combined `<part>_set<set>.png` is the old one-image format and gives every
  material in the part the same 16-colour row. Convert it with
  `jmpped_textures.py` (wizard option 5) into one `_t<id>` crop per material.
- Each crop records `display_clut` (its texture row). A crop with ≤16 colours is
  preserved exactly; more colours are median-cut to 16, then the row and page
  indices are written automatically.

Edit the per-material crop. Do not edit the indexed page or palette strip unless
you are repairing page-level data; the crop is the one image the baker expects.

Page sets are disjoint per entry:

| Entry | Torso/arms | Head | Legs/hands |
| ---: | ---: | ---: | ---: |
| 0 (Jericho) | 23 | 25 | 2 |
| 1 | 26 | 27 | 3 |
| 2 | 28 | 29 | 4 |
| 3 | 30 | 31 | 5 |
| 4 | 32 | 33 | 6 |
| 5 | 34 | 35 | 7 |
| 6 | 36 | 37 | 8 |
| 7 | 38 | 39 | 9 |

The allocation is `ENTRY_PAGE_SETS` in `jmpped_common.py`; `add-character` remaps
every `s<set>_t<id>` material and crop to the new entry's sets.

## 7. Builder rules and refusals

The builder fails with the ped/part/source named rather than silently dropping
data:

- Only FT3/FT4 pedestrian polys are rebuilt; a model with normal tables or an
  unsupported poly kind is refused.
- Faces must be triangles or quads, and must keep a material named
  `s<set>_t<id>` (textured) or `color_R_G_B` (flat).
- The model must match the slot's base model name and the entry must claim all
  14 slots exactly once.
- A referenced page set must belong to that entry; unknown pages are refused.
- Both `models/blender` and `models/engine` changed is ambiguous and refused; similarly a
  full page and one of its crops together.
- `offset`/`scale` must be three finite numbers; a zero scale is refused.
- A transform-only change still rebuilds the part, even when both OBJ copies
  match their hashes.
- No-edit v3 output is byte-identical to `base.LEV`; the v5 writer preserves
  untouched entry 0 model bytes and rebuilds later entries to remap page sets.
- After writing, the builder re-reads the archive and checks version, entry
  count and 14-model shape before reporting success.

## 8. Engine load path

`JerPedImportBaked()` opens `DRIVER2\LEVELS\JMPPED.LEV` and accepts v3 or v5.
The v5 parser (`JerPedImportBakedV5`) validates:

- entry count 1..`JER_PED_MAX_ENTRIES` (8)
- each entry's name, exactly 14 model blobs, and up to 16 page records
- page and model bounds before allocating

It builds each entry's models into:

- `pmJerichoModels[entry][0..5]` — the six JERI parts
- `pmJerichoLegModels[entry][0..5]` — Tanner legs/feet
- `pmJerichoHandModels[entry][0..1]` — Tanner hands

and exposes `JerPedEntryCount()`, `JerPedEntryName(entry)` and
`JerPedModelForEntry(entry, slot)`. The selected entry is carried per
pedestrian in `PEDESTRIAN.pedEntry`; `SetSkelModelPointersForEntry(type, entry)`
binds `Skel[].pModel` to that entry's banks before the draw.

The v5 import uploads every entry's pages through
`JerPedUploadBakedPage()` and releases them with `JerPedReleasePins()` on
`JerPedImportReset()`/level change. The live-Havana fallback uses
`JerPedPinTpages()`/`JerPedPinsReady()` instead. `JerPedImportReady()` means the
bake/v5 models and pages are in place; the mp apply path waits for it.

`JERICHO/build.py` copies `tools/JMPPED.LEV` to
`src_rebuild/bin/<config>/DRIVER2/LEVELS/JMPPED.LEV` (and to a runtime JERICHO
beside the exe when present). A missing bake is not a build failure; the engine
falls back to Havana's streamed pages, and otherwise to Tanner.

The mp module applies the stored `mp.ped_model` in **single player** as well as
in a session. A solo level warms the archive at start through
`MpPedArchiveEnsure()` only when the stored choice is a custom entry (a Tanner
run loads no ped pages; Jericho uses Havana's native models, or the bake on a
level that does not have them). The first cycle of
`Player Options → Preferred Character` also imports on demand. Every level start
calls `MpPedArchiveReset()` first, because `InitTanner` has already dropped the
imported models and palette pairs. `MpPreferredCharacterTick()` then applies the
effective entry to `player[0].pPed` on the first sim frame the player is on foot.
The engine itself needs no extra code: `DrawTanner`/`DrawCharacter` already bind
the per-ped `pedEntry`.

## 9. Adding a new selectable entry

```sh
JMPPED.bat add-character --name "Jericho (Driv3r)"
# or: python jmpped_add_character.py --name "Jericho (Driv3r)"
```

`add-character`:

- copies the source entry's 14 slot OBJs, three pages, palette strips and
  bindpose into `characters/<slug>/`
- assigns the next entry page-set triplet from `ENTRY_PAGE_SETS`
- rewrites every `s<set>_t<id>` material and crop to those sets
- adds the entry to `project.json` and writes `characters/<slug>/character.json`

Then edit the copied `model.obj` and textures, set per-part
`offset`/`scale` where proportions need it, and run `bake`. The v5 archive
carries the entry name; the engine sees it as `JerPedEntryCount()`/`Name()`, and
the mp picker exposes it as `MP_PED_MODEL_*` id `entry index + 1`.

### Registering a completed entry folder

If `characters/<slug>/` already contains `character.json`, all 14 `models/engine|edit` parts,
the three pages/palette strips and the bindpose:

```sh
python jmpped_register.py --folder characters/<slug>
# or drag the folder onto REGISTER_JMPPED.bat
```

It validates the 14 slots, the three page sets, the page/crop/bindpose files and
the entry index → page-set allocation, adds or replaces the entry in
`project.json`, builds the v5 archive, and copies the bake to
`tools/JMPPED.LEV` plus the existing `Release`/`Release_dev` `DRIVER2/LEVELS`
directories. `--no-build` registers without baking; `--no-copy` leaves the bake
in the workspace. The folder must be inside the workspace; `add-character` is the
way to allocate a brand-new entry and page-set triplet from an existing one.

Limits and rules:

- at most 8 entries; the ninth is refused
- every entry owns its own three page sets, so pages/CLUTs cannot alias
- every entry must cover all 14 slots; the slot names must match the base map
- the shared skeleton and Tanner bindings are reused; a new entry does not get
  its own skeleton

## 10. Troubleshooting

**Wrong colours.** Edit the per-material crop
(`textures/crops/<part>_set<set>_t<id>.png`). It is auto-palettized into the texture
row recorded in `display_clut`; `palettes/page_<set>.png` shows the rows. A
combined `<part>_set<set>.png` uses one row for every material in that part and
will look wrong when the part has several texture ids — run wizard option 5 or
`jmpped_textures.py` to split it. The page's indexed PNG is internal/base data,
not the editing surface. Pure black is the engine's transparent entry; a texel
that is not pure black is never folded onto it by the palettizer, it takes the
nearest other shade (the next black shade when the crop has one).

**A bake refuses a UV as "outside the crop".** A UV within two pixels of the
crop edge is clipped and accepted — the Jericho Driv3r glasses sit like that on
their 20x8 extra crops. A miss larger than that is still an error: check the
crop rect and the material's page set. `JMPPED.bat textured --check` prints
`UV CLIPPED (n)` for the accepted case and `UV OUTSIDE (n)` only for a real
mismatch.

**A repaint or extra asset did not reach the game.** Extra materials such as
`Glasses` and `Material.001` own a crop in `extra_materials` just like a part,
and the bake applies both. A changed `model_textured.obj` is re-split and every
part is rebuilt; a repaint only needs the crop's hash to differ (an empty
`rgb_sha256` always re-palettizes). If the bake reports no model change, confirm
you edited the character folder the command is pointed at (the default is the
last/custom character).

**Mashed torso/legs.** `JERI_TORSO` is much longer than Tanner's `TORSO` and
extends through the hips/thighs. Fix it with a per-part `offset`/`scale` on the
torso (or split the long coat), not by scaling the whole character. A uniform
height change still needs the skeleton bind offsets plus collision/camera/seat
offsets; the mesh transform alone does not move those.

**Missing entry or page.** Check `project.json` `entries`/`slots`, the entry's
`page_sets`, and the archive version. The engine logs the entry count, model
count and page count it loaded; an entry id beyond `JerPedEntryCount()` falls
back to Tanner/Jericho in mp.

**v3 vs v5.** v3 is one 14-slot character and is still the `base.LEV` format.
v5 is required for more than one entry. The builder writes v5 when the manifest
has `entries`; the legacy v3 path remains for old workspaces.

## 11. Assembly map

| Stage | Command | Writes | Engine reads |
| --- | --- | --- | --- |
| source extraction | `JMPPED.bat extract` | `base.LEV`, `base_v5.LEV`, `project.json`, `character.json`, OBJ/PNG | — |
| combined-model edit | `JMPPED.bat import` / `JMPPED.bat bake` | `models/blender/<part>.obj`, `models/engine/<part>.obj` | — |
| per-part/texture edit | edit files + `project.json` | OBJ/PNG/`offset`/`scale` | — |
| bake | `JMPPED.bat bake` | `JMPPED.LEV` (v3/v5) | `JerPedImportBaked()` |
| runtime copy | `--copy` or `JERICHO/build.py` | `bin/<config>/DRIVER2/LEVELS/JMPPED.LEV` | engine load |
| preview | `JMPPED.bat preview` | `preview.gif` | — |

## 12. Verification

- `python JERICHO/MODS/mp/tools/jmpped_selftest.py --selftest` — archive parse/round-trip.
- `JMPPED.bat rebuild-all` — export, build and preview in one run.
- `python tools/doccheck.py` — links, docs index and counted claims.
- `python JERICHO/build.py game mod mp` — the module build.
- A two-seat Havana rig with `MP_TEST_PED_MODEL` — character sync/apply.
- A single-player run: `MP_TEST_PED_MODEL=2 MP_TEST_ONFOOT=10` with
  `-level havana` — the solo import/apply. Expect
  `ped import: baked JMPPED.LEV v5 loaded (N entries`, `[mp] character: chose`
  and `[mp] character: applied`; with a Tanner choice the run must not import
  the archive at all.
- `MP_TEST_JERICHO` — import the archive without a session.
- A missing `JMPPED.LEV` must leave both seats on Tanner with no crash.

## 13. Glossary

| Term | Meaning |
| --- | --- |
| **character** | one selectable archive entry; the engine API still calls it an entry |
| **part** | one of the 14 models a character owns |
| **slot** | the archive index 0–13 for a part |
| **source model** | Tanner or Jericho extracted from the Driver 2 level (`out/source/<level>/source_models/`) |
| **texture page** | a 256×256 4bpp page with one palette row per texture id |
| **palette row** | one 16-colour CLUT row selected by a material's texture id |
| **crop** | the editable RGB image for one material on a page |
| **project** | the workspace described by `project.json` |
| **bake** | write `JMPPED.LEV` from the project |
