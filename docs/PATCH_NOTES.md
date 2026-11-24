# BAKE patch notes

## Unreleased

### Wheels and portrait live under `textures`, with the page backdrop keys

- `wheels` and `portrait` now sit in the car's `textures` block next to the page ids. A top-level `wheels`/`portrait` still loads and is moved under `textures` the next time the bake stamps the car json; declaring both is an error.
- A wheel entry and a portrait object accept `transparent` (`auto` / `alpha` / `rrggbb`), `transparent_tolerance` and `transparent_chroma`, same meaning as a page. Left out, a wheel behaves exactly as before (top-left colour, tolerance 8) and the page hash does not change, so nothing re-bakes. A portrait key sends the keyed texels to CLUT index 0.

### A Tanner model keeps Tanner's part names

- Slots 0-5 are addressed by number. Their archive names are the base Jericho set (`JERI_*`), but a character built from Tanner's torso/head/arms (tannerd3) names the same slots `TORSO`, `HEAD`, `U_ARM_*`, `L_ARM_*`. The splitter, importer and textured export accept either name, adopt the names the model uses, and switch the bind variant with them, so a Blender export is not relabelled as Jericho.
- A Blender scene that prefixes every object with `JERI_` (including the limbs, e.g. `JERI_CALF_LEFT`) now resolves by stripping that prefix when the full name does not match.
- `jmpped_make_tannerd3.py` writes the Tanner names and the `tanner` bindings, and the per-part caches/crops follow them (`models/blender/TORSO.obj`, not `JERI_TORSO.obj`).

### Palette rows are allocated by the bake

- For a custom entry, `bake` quantises each part's crop and allocates a CLUT row per distinct palette: equal palettes share a row (mirrored arms, the leg set), different palettes get the next free one. The part's model materials are rewritten to the allocated row, and the crop's row in `character.json` is a starting hint rather than a hard mapping.
- Entry 0 (the stock Jericho set) keeps its shipped palette layout byte-for-byte; only custom entries are re-allocated.
- The part caches and the combined `model.obj` no longer need hand-maintained texture ids for a custom character. `allocate_palette_rows()` raises if a page runs out of rows instead of silently overwriting another crop's palette.


### Car-select portraits pack into `<CITY>.RAW`

- `portrait` on `cars/<id>/handling.json` (path relative to that file; optional sibling `.clut` on a pre-packed page) is the author-facing field. An inline `bank.json` car object is the same key and the same packer.
- The bake writes `<CITY>.RAW` beside the LEV/LCF: page at `model-slot * 0x8000` (the in-game model, not the roster cursor). It still writes `<CITY>.portraits/<slot>.raw` and, for a pre-packed page, `.clut`. Not a lump.
- `rebake.bat` copies the RAW with the LEV/LCF and removes a previously installed RAW when this bake declared none.
- A portrait ending in `.png`, `.tga`, or `.bmp` (any case) is encoded to that same 32768-byte page. Pillow opens it, the same way a car texture page is opened. The picture is 256×219 or 256×256.
- A mode-P image whose first 16 palette entries are already the frontend CARBACK portrait CLUT (`CARBACK.RAW` offset `11 * 0x8000`, the row at VRAM `(960, 256)`) is packed by index. It is not re-quantised. `fourbpp.py` RGB555 rounding is unchanged and is not used on this path.
- Any other raster is Bayer-remapped onto that same CLUT. No `<slot>.clut` is written: the page uses the stock car-select palette. A `<slot>.clut` left from an earlier bake is removed when this source has none. A pre-packed `.raw` of exactly 32768 bytes, and its optional 32-byte sibling `.clut`, is still copied unchanged.

### Car banks are generated: no `donor/` (`carbank_bake.py`, `exportd2.py`, `make_new_bank.py`)

- A declared bank bakes from `bank.json` + `cars/` (+ `shared/`) alone. The
  working manifest starts empty; no donor or previous build manifest is read.
- `build/` now always gets generated `textureinfo.bin` (from the baked page
  sizes, wheel islands named WHLCLEAN/WHLBOTH), `texturenames.bin`,
  `modelnames.bin` (car id per slot) and `pallet.bin` (from the pages).
- `palette_from_pages` defaults to on for declared banks; `false` is refused.
- Poly tints are no longer read back from a donor LEV (that lookup never
  matched an exported bank's file names anyway).
- `bank_layout()` returns `(bank_dir, build_dir)`; `donor_dir` and
  `declare.donor.lev` are ignored. `declare.donor` is a label.
- Export / `make_new_bank` decode into a temp scratch folder and leave no
  `donor/` or `*.export.json` in the bank; `--rebuild-bank` on a `bank.json`
  bakes it generatively.
- `preview_car.py` takes wheels from `handling.json` only (else bbox guess).
- New `test_generated_lumps.py`.

## 0.12 (JERICHO v0.12.0)

### Car bank tools: `make_*` naming + wizard (`tools/car/`)

- Bank-creating tools all start with `make_` (callers, README and bank-schema updated; no shims - nothing
  outside BAKE called the old names):
  `basebank.py` -> `make_new_bank.py`,
  `rebake_install.py` -> `make_write_scripts.py`,
  `rebake_template.bat` -> `template_rebake.bat`,
  `playtest_template.bat` -> `template_playtest.bat`.
  Templates stay `template_*.bat`; per-bank outputs stay `rebake` /
  `playtest` / `preview` / `wizard`.
- New `template_preview.bat` -> each bank's `preview.bat`: lists the bank's
  slots, asks which to load and opens an endless `preview_car.py` spin until
  Esc / Q / window close, then asks again (Enter quits). No bake, no game.
- New `template_wizard.bat` -> each bank's `wizard.bat`: one menu for every
  stage (export, import an OBJ, bake, preview by slot, playtest, register
  city, refresh scripts, open folder).
- New `tools/car/make_wizard.bat` (hub): create a bank from a donor car, manage an
  existing bank (opens its wizard), or export a Driver 2 city's cars.
- `make_new_bank.py` now writes rebake / playtest / preview / wizard beside
  the new bank. Existing banks get the new ones with
  `python make_write_scripts.py <bank>/bank.json` (missing files only).

### Ped bake (`tools/ped/jmpped_player.py`)

- **JMPPED.LEV is built from the player's own game.** The auto re-bake
  (`BAKE_D1_CARS.bat/.sh`, BAKE_VERSION 10) now runs `jmpped_player.py`
  first: it extracts the stock Jericho parts and Tanner's limbs from
  `DRIVER2/LEVELS/HAVANA.LEV` and writes `DRIVER2/LEVELS/JMPPED.LEV`, so
  the Jericho model does not need to be distributed. Driv3r Jericho is not
  part of it. Missing `HAVANA.LEV` fails with a clear message (exit 2).
- It works in a throwaway project, so the authoring `project.json`,
  `characters/` and last-used state are never touched. The ped step needs no
  Driver 1 data; without `DRIVER/LEVELS` the cars are skipped, not failed.

### Car bake (`carbank_bake.py`, `bankjson.py`, `obj2geom.py`)

- **Wheel texture islands use your overrides.** Islands are keyed by a
  content hash (sha256) and the first one found wins, so a texture in
  `shared/textures` now beats the stock PAGE_20 copy instead of being
  silently replaced by it.
- **The donor folder is no longer baked.** Each car's own
  `cars/<car>/handling.json` is the source of truth for handling and the
  cosmetics (LCF) record; donor values are not used.
- **256x256 pages no longer bake white.** The page list is rebuilt and
  `_sync_page_sizes` remaps texture faces to the page's real size, so
  Havana-style 256x256 pages keep their art.
- **`--flip-winding` keeps editor normals outward.** Author with outward
  normals; bake/export flips to the engine's inward winding. Per-face
  materials and kinds now stay with their faces when flipped (they were
  being reversed with the face list).
- `bankjson.dumps_compact` writes bank.json in a compact, diff-friendly form.

### Export (`exportd2.py` + new `exportd2_assets.py`, `rebake_install.py`, `register_city.py`)

- `exportd2` writes a complete, rebakeable vehicle package (models, pages,
  materials, sounds, per-car `handling.json`, donor manifest) so a car can be
  edited and rebaked from obj/png/wav/json alone.
- New `--name` and `--bank-per-car` options.
- `rebake.bat` / `playtest.bat` are generated from one canonical template
  (`rebake_template.bat`, `playtest_template.bat`) via `rebake_install.py`.
- `register_city.py` registers an exported city/LEV with a game install.

### Preview (`preview_car.py`)

- Wheel positions come from the car's handling, at bake scale (new
  `--bake-scale`), and the body offset uses the same cog/hub formula as the
  game, so the preview matches what you see in game.
- Wheels spin faster, closer to the in-game fastwheel.

### Sound (`vag.py`)

- `clipped` now counts only samples pinned at the int16 edge; samples over
  full scale mid-chain are reported separately as `over` (the clamp happens
  at encode). `remove_dc` no longer clamps. New `scale()` helper. Tests
  updated in `test_vag.py`.

- jmpped_player: `--characters DIR` imports every character folder in DIR (e.g. MODS/mp/peds/jerichod3, tannerd3) after the stock Tanner + Jericho rip from HAVANA.LEV, so first-launch BAKE_D1_CARS ships the MP ped pack. Failed imports warn and skip.


- JMPPED v5: entries may carry a 15th trailing slot, HIPS. The bake copies Tanner's HIPS from the source level onto each entry's legs page set, so a JMPPED character has its own hips (drawn on LHIP, tinted with the body) instead of borrowing resident Tanner's. 14-model entries still load. Engine side ships in the superproject 0.12.
- JMPPED toes now cross over like stock Tanner (LTOE draws FOOT_RIGHT, RTOE draws FOOT_LEFT); JERICHO_BINDINGS / skeleton export match the engine.
- jmpped_entries.map_page_sets composes an existing texture_page_map when a character is re-allocated to other page sets (an imported character colliding with an existing slot kept pointing at its old legs set).
- New jmpped_make_tannerd3.py: builds the tannerd3 character folder (Tanner's TORSO/HEAD/arms from HAVANA.LEV in the JERI slots of a Jericho clone).
- tools/ped/JMPPED.LEV rebaked (only change: the HIPS slot per entry).
