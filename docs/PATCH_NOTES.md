# BAKE patch notes

## 0.12 (JERICHO v0.12.0)

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