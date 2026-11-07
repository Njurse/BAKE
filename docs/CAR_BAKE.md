# Car bake — palette, preview size, sounds

Root causes for the imported-car colour, preview-scale and sound reports, and
what the bake does about each. Output stays a carhacks LEV/LCF. `fourbpp.rgb555`
is unchanged (a different rounding shifts every bank already baked).

## 1. Palette pagination and colour crush

Two different mechanisms were being treated as one.

**Texture id collapsed onto row 0** — `carbank_bake.car_blocks`. A declared page
was baked with one CLUT row and every poly on that set was rewritten to
`id = 0`. The engine picks the row with `texture_cluts[set][id]`
(`exportd2.page_png` says the same thing: painting the whole page with row 0
garbles every sprite but the first). Firebird's lamps are `set65_id13`. Forcing
them onto id 0 paints the lamps with the body row. That is a separate bug from
the old "one mesh, many ids" note: the mesh was right, the bake threw the id
away. A page whose snapped colours already fit in 16 entries still takes this
path, so an existing one-row bank does not change.

**One 16-entry row for a page that does not fit** — `carbank_page.bake_page` →
`fourbpp.paletteize`. A 256-colour PNG that snaps to 16 or fewer RGB555 colours
is kept exact (`count_snapped` / `should_split`). Past that, `split: "auto"`
(the default) groups the page's UV islands (`plan_islands`) so each group gets
its own CLUT row and the poly's id follows the island (`_id_at`). The first
group of a source id keeps that id, so `set65_id13` stays 13. `split: "off"`
is the old single-row quantize. `max_error` accepts one row when the quantize
is no worse than that many channels.

**civ_clut row budget** — not a bake that only emits eight rows. `bake.build_pallet`
emits every `(set, id)` the geometry names. `CarPalIndexInCity` only searches
`carTpages[city][0..7]` (`docs/DRIVER1.md` §7), so a set past the eighth draws
row 0, the first page's palette. A D1 city bakes 11–13 sets; the pool can name
more. `carbank_page.civ_row_map` records that, `build_pallet` prints the
overflow, and a declared bank writes `clut-dump.txt` (set → row, then each CLUT
as hex). `declare.clut_rows` changes the diagnostic only. The bake does not
drop or renumber sets: a bank that already loads would move.

`carbank_palette.rows_from_pages` emits one LUMP_PALLET record per CLUT row,
`texnum` = the texture id. A one-row page is still a single texnum-0 record.

**Leftover sprites on a retail page.** A Driver 1 page (REDBUICK `PAGE_1` is
the one that shows it) still carries sprites the mesh does not sample. A
whole-page quantise spends the body's 16 CLUT entries on those sprites and
the body crushes. `split: "auto"` now also looks at texels outside the UV
coverage (`carbank_page.leftover_blobs`). When those colours do not fit in
the body's row they are paginated onto the next texture ids, each with its
own CLUT row. The body polys stay on the id their material named. The
backdrop (the page's top-left colour) is not a sprite. `split: "off"` keeps
the old whole-page quantise.

## 2. Preview scale (REDBUICK)

The bake multiplies Driver 1 vertices by `2048/3000` (`bake.build_model_record`,
`docs/DRIVER1.md` §8). The LEV the game reads is already in the Driver 2 band
(D1 median length 1104 → about 754, against D2's 752).

`preview.py` `render`, `textured.py` `render` and `spin.py` `refs_for` used to
fit **each mesh to its own span**. An unscaled body and a correctly scaled one
filled the same cell, so the preview could not show the in-game size. They now
share the projected span of the Driver 2 median box (284×176×752). A mesh still
at D1 size draws about 1.47× larger.

`preview_car.py` reads the OBJ, not the LEV. It treated every mesh longer than
50 units as already engine-sized (`S = 1`), then divided handling `wheelDisp`
(engine units) by that. A REDBUICK OBJ is ~1100 units long and the colBox is
the scaled half-extent, so the wheelbase landed at `2048/3000` of the body and
the car read as huge next to its wheels. `preview.infer_bake_scale` uses the
colBox ratio (or a long axis past 980) and returns `2048/3000` for that OBJ,
`1` when the OBJ already matches the colBox.

The earlier "huge black wheels" note was the opposite case (body too small for
the wheel mesh). This one is the body, in the authoring preview.

## 3. Sounds

`tools/common/vag.py` packed sample 0 in the **high** nibble. `vag.decode`
agreed with itself, so the round-trip tests passed, and the engine did not:
`add_car_sounds.decode_vag` and real VAG are low-nibble first. Every pair of
samples was swapped. That is the garbled / crushed playback on a bank baked
from WAV (Firebird). Loop and end flags are byte 1 of the block; `vag.encode`
still writes `0x04` on the loop start and `0x03` / `0x01` on the last block,
and `add_car_sounds._scaled_vag` still copies the source block's flags after
the gain re-encode.

Level, RMS and the 6% dead band are unchanged (`vag.level`, `docs/SOUNDS.md`).
A recording that is already limited (Firebird engine crest ~2.0) stays limited;
the nibble order was what made a clean encode play back destroyed.

## 4. Paletted-image paint

`textures.<set>.paints` accepts a hex colour (the existing hue shift,
`carbank_palette.paint_row`) or `{"image": path, "id": N, "panel": [x0,y0,x1,y1]}`.
`paint_from_image` sets each CLUT index to the colour the image shows on the
texels that already use that index. Indices are not rewritten. `id` restricts
the paint to one texture row (Firebird lamps). See `bank-schema.md`.

## 5. The vehicle preview shows the baked page

`preview_car.py` used to upload the MTL's authored page image and clear every
black texel to alpha 0. That showed colours the game cannot draw (an 8-bit
atlas, not a 4bpp page) and made genuinely black paint invisible. The preview
now runs the page bake in memory before it uploads anything:

- per (set, texture id) it draws the page's index image painted by THAT id's
  CLUT row - the row `texture_cluts[set][id]` selects - so a split page and its
  colour crush are visible in the preview;
- pages come from `carbank_bake.bake_declared_page`, the same helper
  `_sync_declared_pages` writes, including the wheel-sprite composite;
- alpha 0 is only for the bake's cleared CLUT entries (or the source PNG's
  real alpha). A black texel the bake did not clear is opaque black;
- `--palette N` shows a `civ_clut` paint column (0 = the page's own colours)
  through `carbank_palette.paint_rows_for`, shared with LUMP_PALLET;
- a legacy bank with no declaration falls back to `pages/PAGE_N.bin`, then to a
  16-colour quantise of the material image - never the raw authored art.

The startup report names each processed page, its row count and its keyed-texel
count, so "the preview looks different" can be traced to a page bake rather
than guessed at.

`PAGE_<set>.png` and the preview use the same convention: a COLOUR-KEYED
backdrop is bright magenta (`#ff00ff`) with alpha 0; real PNG alpha keeps the
page's colour with alpha 0. The baked `.bin` still writes `0x0000` at the keyed
entry, which is what the engine reads.

## Verification

```text
python3 -m unittest test_page_split test_preview_scale test_carbank_page \
    test_fourbpp_palette test_bankjson test_sound_knobs test_preview_pages -q
python3 -m unittest test_vag test_fourbpp -q          # from tools/common
```

In the bank, after `carbank_bake.py bank.json`:

- `build/clut-dump.txt` — which set lands on which civ row, and the swatches.
  `OVERFLOW` means that set draws the first page's palette in a stock
  8-row engine.
- Preview a REDBUICK (or any D1) OBJ with `preview_car.py`. Wheels from
  `handling.json` should sit on the body's wheelbase, not a short one.
  `preview.py` / `textured.py` print the longest axis next to D2's 752.
- Sounds: rebake, then in JERICHO grep for
  `gamesnd: resident slot N uses the imported model bank`. Engine and idle
  should loop (flags byte still `0x04` / `0x03`); a one-shot horn ends (`0x01`).
  Compare a decoded VAG against `add_car_sounds.decode_vag`, not only
  `vag.decode`.

Full in-game colour and pitch still need a JERICHO pack: load the bank, look
at lamps versus body on the Firebird, and listen to the rev loop. `clut_rows`
must match that build's `carTpages` second dimension or the dump's "fits" line
is only true on paper.
