# bank.json — what a person writes, and what the bake computes

A bank folder holds one car bank: `FIREBIRD.LEV` + `FIREBIRD.LCF` (the loadable
pair, installed by the bake), the author's sources, and `bank.json`.

**`bank.json` is input only.** It holds `city` / `lev` / `lcf` (what the bank is
called), the optional layout keys below, and `declare` (what the author wants:
sources, layout, sounds). The bake reads and validates it and never writes it.
What the bank turned out to be - size, included sets, pages, palette records,
checks, warnings - goes to `<NAME>.report.json` (the bank's name, e.g.
`<NAME>.report.json`) beside the built manifest.
(Older banks still carry that computed half at the top level of `bank.json`;
it is ignored and can be deleted.) A bank with no `declare` block is a legacy
bank: its manifest is reproduced as it stands (migration only).

## Folder layout: inputs vs outputs

A declared bank is **generated**. Every file has one job:

```json
{ "city": "FIREBIRD", "lev": "build/FIREBIRD.LEV", "lcf": "build/FIREBIRD.LCF",
  "build_dir": "build", "manifest": "FIREBIRD.json", "declare": { ... } }
```

| path | who writes it | holds |
| --- | --- | --- |
| `bank.json` | the author | the declaration: name, slot order, cars |
| `cars/<id>/` | the author | `handling.json` (OBJ paths, `textures`, wheels, sounds, `portrait`, the `handling` LCF record), OBJ/MTL, page images, the portrait page |
| `shared/` | the author | page images / sounds more than one car names |
| `build_dir` | the bake, every time | `pages/`, `models/`, `pallet.bin`, `textureinfo.bin`, `texturenames.bin`, `modelnames.bin`, the built `<NAME>.json`, `<NAME>.report.json`, the LEV/LCF, and — when a car declares a portrait — `<NAME>.RAW` plus `<NAME>.portraits/` |
| bank root `<NAME>.LEV/.LCF` | `rebake.bat` | an install copy of the build pair, plus `<NAME>.RAW` and `<NAME>.portraits/` when the bake wrote them. Never edited |

**There is no donor folder.** The bake reads `bank.json` + `cars/` (+ any
`shared/` file they name) and nothing else:

- pages: baked from each car's `textures` map; the declared sets are the page list
- palette: always from the pages (`palette_from_pages`, default and required)
- `textureinfo`: generated from the baked page sizes (+ named wheel islands)
- `texturenames` / `modelnames`: generated from the wheel islands / slot order
- models: the declared OBJs; poly tints come from the OBJ/MTL, not a source LEV
- handling / LCF: each car's `handling.json` `handling` block

`build/<NAME>.json` is a record of what was assembled, never read back as an
input, so deleting `build_dir` is always safe. Left out, `build_dir` is the bank
folder itself (the old flat layout). An old `donor_dir` key is ignored.

`make_new_bank.py` / `exportd2.py` decode the source level into a scratch
folder outside the bank, write `bank.json` + `cars/` + `shared/`, bake, and
delete the scratch: the bank they leave behind is already donor-free.

### Migrating a bank that has `donor/`

1. In `bank.json` delete `donor_dir`, and `declare.donor.lev` (keep
   `declare.donor.city` as a label if you like).
2. Make sure every page the car samples is declared in its `textures` map
   (the donor no longer supplies undeclared ones).
3. Rebake. When `bank ok` prints, `donor/` can go.

## Schema

```json
{
  "city": "FIREBIRD",
  "lev": "FIREBIRD.LEV",
  "lcf": "FIREBIRD.LCF",

  "declare": {
    "schema": 1,
    "name": "FIREBIRD",

    "donor": { "city": "CHICAGO", "model": 1 },

    "order": [
      { "slot": 1, "car": "FIREBIRD_model1" }
    ],

    "cars": {
      "FIREBIRD_model1": {
        "clean":   "source/Firebird_high.obj",
        "damaged": null,
        "low":     "source/Firebird_low.obj",

        "textures": { "1": "source/firebird.png" },

        "wheels": {
          "clean":   { "png": "source/wheelclean.png", "set": 2,
                       "island": [0, 0, 60, 60] },
          "fast":    { "png": "source/wheelfast.png", "set": 3,
                       "island": [0, 0, 60, 60] }
        },

        "sounds": {
          "engine": "source/engine.wav",
          "idle":   "source/idle.wav",
          "horn":   "source/horn.wav",
          "siren":  null,
          "cop":    false
        },

        "palette": { "source": "raw/pallet.bin", "row": 0 },
        "remap":   { "set": { "1": 1 }, "id": { "0": 0 } }
      }
    }
  }
}
```

Every path in an inline car object is relative to the folder holding `bank.json`. A `cars` entry may instead be a path to `cars/<id>/handling.json`. Paths inside that file — including `portrait` — are relative to the handling file. Keys the bake does not read (`handling`, `raw`, `notes`) stay in the file and are ignored.

### Portrait

Optional. Put `portrait` on the car in `cars/<id>/handling.json`, **under `textures`** (`"textures": {"portrait": ...}`). A top-level `portrait` is the older spelling: it still loads, and the next bake moves it under `textures`. That is the author-facing field. An inline car object in `bank.json` accepts the same key and goes through the same packer; prefer the handling file, which is where the rest of that car already lives.

The value is a path, or an object `{"png": path, "transparent": ..., "transparent_tolerance": ..., "transparent_chroma": ...}`. The backdrop keys are the page ones (below) and apply to a `.png`/`.tga`/`.bmp` portrait only: every keyed texel is written as palette index 0, the stock car-select CLUT's dark/transparent entry (`auto` = the top-left texel, `alpha` = the PNG's alpha channel; per-channel tolerance defaults to 8). A pre-packed page that names a key is refused. The path is relative to the handling file (or to `bank.json` when the car object is inline). A source under `work/`, `pages/` or `build/` is refused, same as a texture.

A name that does not end in `.png`, `.tga`, or `.bmp` is a pre-packed page. It must be exactly `32768` bytes of 4bpp indices, and it is copied unchanged — the bake does not quantise it, and `fourbpp.py` RGB555 rounding is not involved. A `<same-name>.clut` of `32` bytes beside that file is optional and is copied too.

A `.png`, `.tga`, or `.bmp` (any case) is encoded at bake time. Pillow opens it, the same loader a car texture page uses. The picture is `256×219` (the rows the frontend uploads) or one full `256×256` page. A `256×219` picture is padded to 256 rows with index 0.

A mode-P image whose first 16 palette entries already are the frontend portrait CLUT is packed by index and is not rematched, so those portraits are not colour-crushed. That CLUT is the 32 bytes at `CARBACK.RAW` offset `11 × 0x8000`, the row `LoadBackgroundFile` leaves at VRAM `(960, 256)` — the same 16 colours, in order. A PNG saver may keep the low 3 bits; the match is on `(channel >> 3) << 3`. A palette slot above 15 is kept only when its colour is already one of those 16 (a duplicate near-black folds to that entry, not to its low nibble).

Any other raster is remapped onto that same CLUT: a 4×4 Bayer stipple between the two nearest entries, and a texel that is already a CLUT colour stays there. This is the stock car-select palette, not a new one. No `<slot>.clut` is written for a raster. A private CLUT belongs beside a pre-packed page only; shipping one next to a matched portrait would paint it through a different palette.

The bake writes these beside the built `<CITY>.LEV` / `<CITY>.LCF`. `rebake.bat` copies them next to the install copy and into the game mirror, which is where the frontend looks: beside the `.LEV` the city registry opens. None of this goes inside the LEV.

```
<LEV stem>.RAW
<LEV stem>.portraits/<slot>.raw
<LEV stem>.portraits/<slot>.clut    optional
```

`<LEV stem>.RAW` is one file in the same shape as stock `DATA\CARS\CCARS.RAW`: pages of `32768` (`0x8000`) bytes. **Page `slot` is at offset `slot * 0x8000`.** `slot` is the model number in `order` — the number the frontend passes when it draws the icon — not the dense car-select cursor. Miami's cop is model 1 and roster slot 0; its page is at `1 * 0x8000`, the same place as `1.raw`. A model with no portrait is a zero page. The file is only as long as one past the highest declared slot, and a rebake that declares no portraits deletes it.

The loose `<slot>.raw` is that same page. The frontend uses it when `<stem>.RAW` is missing or that page is all zero, so a hand-dropped `*.portraits` folder still works and a game that does not read the RAW yet still shows the icon. Dropping the folder there by hand works without a declaration.

The CLUT is never inside the RAW. It stays `<slot>.clut`, and the frontend applies it whichever pixel source won. A raster portrait does not write `<slot>.clut`; the indices are for the stock palette. A pre-packed page writes one only when the source had a sibling `.clut`. A `<slot>.clut` left from an earlier bake is removed when this bake's source has none.

| file | size | what it is |
| --- | --- | --- |
| `<stem>.RAW` | `(highest slot + 1) * 32768` | packed 4bpp pages. Index is the model slot. An all-zero page is empty |
| `<slot>.raw` | exactly `32768` (`0x8000`) | the same page, loose. 256×256, 128 bytes a row. The frontend uploads the top **219** rows and draws a **255×219** sprite, the same rectangle as one slot of `CCARS.RAW` |
| `<slot>.clut` | exactly `32` bytes | 16 little-endian RGB555 colours. Uploaded over the portrait CLUT at VRAM `(960,256)` after `CARBACK.RAW`. Omit it to keep the stock car-select palette |

### `order[]`

The bank's model slots, in engine order: `slot` is 0..12 and unique, `car` names
an entry in `cars`.
Slots the bank does not carry must be left out rather than set to `null` — the
bake writes `-1` for them.

### `cars.<name>`

| key | required | meaning |
| --- | --- | --- |
| `clean` | yes | the undamaged body, OBJ |
| `damaged` | no | the crumpled body; absent means the clean body |
| `low` | no | the low-detail body (LOD); absent means the clean body |
| `atlas` | one of | a single atlas PNG covering every page, in page order |
| `textures` | one of | `{set id: image}` per texture page. Use this instead of `atlas` when the pages are separate images |
| `wheels.clean` | no | the road wheel: `{png, set, island}` — see below |
| `wheels.damaged` | no | the buckled wheel — **omit it cleanly** when the car has none; the bank writes `-1` and the engine keeps the host wheel texture for that variant (`models.c:2743`) |
| `wheels.fast` | no | the high-speed blur wheel |
| `sounds` | no | see below |
| `palette` | no | a CLUT source for a car whose colours are not its pages' own |
| `remap` | no | `set` / `id` maps applied to every poly as it is read |
| `portrait` | no | a car-select portrait, usually in `handling.json`. See *Portrait* above. A pre-packed page is copied; a `.png`, `.tga`, or `.bmp` is encoded onto the CARBACK portrait CLUT. The bake packs the page into `<stem>.RAW` and writes the loose page |

### `textures`

`textures` is the one block for everything the bake turns into texels. Numeric keys are page set ids; two reserved names sit beside them:

| key | meaning |
| --- | --- |
| `wheels` | `{clean, damaged, fast}`: each a path or `{png, set, island, transparent, transparent_tolerance, transparent_chroma}`. The wheel's backdrop is flood-filled inward from the sprite's border, so a black tyre on a black backdrop stays solid. With no key declared: the sprite's top-left colour, per-channel tolerance 8, alpha winning when the art has any. `transparent`/`_tolerance`/`_chroma` mean what they do on a page, see below |
| `portrait` | the car-select portrait, see *Portrait* |

A top-level `wheels` or `portrait` in `handling.json` is the older spelling. It still loads; the bake moves it under `textures` (and says so). Declaring both spellings is refused.

A declared page or sprite may NOT live under `work/`, `pages/` or `build/`: those are
what the bake writes, so a source there is a previous bake's output - and its
hash would keep looking current however the art changed. That is refused by
name.

A car names the image for each texture page it uses, keyed by **set id**:

```json
"textures": {
  "1": "textures/firebird.png",
  "2": { "png": "work/pages/page2.png", "transparent": "auto",
         "transparent_chroma": 128 }
}
```

The image is the source of truth. There is **no `pages` key** in a declaration:
a page's PNG preview is something the bake writes back, so declaring it would
make an output an input - and re-baking a page from its own preview is generation
loss. `pages[]` exists only in the computed half (the manifest), where it records
what the images produced: size, palette row, the image's sha256, the preview path.

| key | required | meaning |
| --- | --- | --- |
| (set id) | yes | the key IS the page set. `"1"`, `"2"`, ... � the same ids the models' `setN_idM` materials name |
| `png` | yes | the image that page is baked from |
| `transparent` | no | backdrop colour to make transparent: a hex colour (`ff00ff`), `auto` (the image's top-left pixel) or `alpha` (the PNG's alpha channel). There is no per-texel alpha here - the engine's marker is a CLUT entry of `0x0000` - so a sprite on a flat backdrop is an opaque square without this |
| `transparent_tolerance` | no | per-channel slack for that match (0..255) |
| `transparent_chroma` | no | match by COLOUR CAST instead: a texel whose `(r-g, b-g)` is within N of the backdrop's. Catches a soft blended edge without eating grey art |
| `protect` | no | colours the page's 16 MUST keep (`["b8b8c0", ...]`); the quantiser evicts the least-used colour instead |
| `palette` | no | exactly 16 hex colours: the page is re-indexed to them and nothing is quantised |
| `split` | no | `"auto"` (default) or `"off"`. `auto` gives each panel its own CLUT row when the page's snapped colours do not fit in one 16-entry row. `off` is the old single-row quantize. A page that already snaps to 16 or fewer colours is never split |
| `max_error` | no | with `split: "auto"`, accept one row when the worst channel error of that quantize is at most this (0..255). Leave it out to split whenever 16 entries cannot hold the colours exactly |
| `paints` | no | colour variants, in order: a hex colour or a paletted image (max 5). Needs `palette_from_pages`. See *Paints* below |
| `paint_body` | no | which page colours a hex paint recolours (`["c0e0f0", ...]`). Default: every mid/light blue entry. Image paints do not use this; they copy indices |
| `body_rows` | no | Driver 1 page CLUT indices that are one body colour, in swap order: `[base_id, extra, ...]`. The first is the texture id already in `civ_clut` slot 0. The rest fill slots 1..5. A one-element list disables auto-detection. See *Paints* below |

#### Paints (colour variants)

The short way: one list for the whole car, in `handling.json`:

```json
"textures": {
  "54": "textures/PAGE_54.png",
  "paints": ["red", "blue", "silver"]
}
```

* Colours are names (`red maroon orange yellow gold cream beige brown green darkgreen
  lime teal cyan blue navy lightblue purple pink white silver grey black`) or `rrggbb`.
  Up to five. Colour 0 is always the car's own paint; these are colours 1..5.
* The list goes to every page whose image is in **the car's own folder**. Shared pages
  (`../../shared/...`, the stock wheel/trim pages several cars draw) are left alone,
  because a page's colour columns belong to every car that uses it; the bake says which
  it skipped. A page's own `"paints"` always wins.
* What gets recoloured is **measured**: the hue covering the most of the page is the
  body, and only entries near that hue move (shading, glass, trim, lights and anything
  `protect`ed stay). The bake prints what it found, e.g. `set 54: paint body detected as
  yellow (55 colour(s), 81% of the page)`. A page with no coloured body - a white, grey
  or black car - is left unchanged and the bake says so; list its body colours in
  `paint_body` to paint it.
* Every paint gets a preview beside the page preview:
  `build/pages/PAGE_<set>_paint<k>_<name>.png`. Look at those before driving it.

The per-page form below is still there for full control (image paints, one panel).

    "1": { "png": "art/textures/firebird.png",
           "protect": ["ebebe8", "4b0a08"],
           "paints": { "yellow": "e0c020", "red": "c02020",
                       "green": "20a040", "grey": "909090" } }

The engine keeps six colour columns per texture (`civ_clut[row][tex][0..5]`).
Column 0 is ALWAYS the page's own colours - the original paint. Each declared
paint becomes one more palette record (lump `palette` k -> column k+1), so the
example above gives 0 blue, 1 yellow, 2 red, 3 green, 4 grey.

A hex (or named) paint copies the page's 16-colour row and recolours only the BODY
entries: hue and saturation from the paint, shading kept (the brightest body entry
lands on the paint colour's own lightness). Never touched: `0x0000` (transparent),
`protect` colours (lights), and anything that is not body. With no `paint_body` the
body is the page's dominant hue, measured as above (it used to be "every mid/light
blue entry", which painted nothing on a car that was not blue). If the measurement
picks the wrong thing, list the body colours explicitly in `paint_body`.

An **image paint** is a recolour of the same atlas (or of one panel), already
paletted or close to it. Each source index takes the colour that image shows
on the texels which use that index, so the 4bpp indices stay put and only the
CLUT row changes. `id` limits it to one texture id (`set65_id13`'s lights);
`panel` is `[x0, y0, x1, y1]` when the image is that rectangle rather than a
full page. Whole-atlas and per-panel both bake a normal 4bpp page plus a CLUT
the engine can draw.

```json
"paints": {
  "yellow": "e0c020",
  "night": { "image": "art/firebird_night.png" },
  "lights": { "image": "art/firebird_lights.png", "id": 13 }
}
```

A Driver 1 page often already has those colours as extra CLUT rows (same
trim, glass and lights, different body shades). Rows the mesh does not use,
and that are a recolour of a row it does, are written into the same slots:
`civ_clut` column 0 stays the page row for that texture id, and the extras
take columns 1..5 (`LUMP_PALLET` palette 0..4, which `ProcessPalletLump`
stores at `palette + 1`). `cp->ap.palette` cycles them. Five extras fit; a
sixth is reported and dropped, because column 5 is the last and a higher
palette clamps there.

Name the rows when detection is wrong, or pass a one-element list to keep
the page's own colours only:

```json
"body_rows": [0, 4, 5, 6]
```

The first index is the texture id already in civ slot 0 (the body's
`texture_cluts` row). The rest are the paint columns, in swap order.
When `paints` is also set, those colours fill slots 1..5 and `body_rows`
is left unread.

`declare.clut_rows` (default 8) is how many `civ_clut` rows this engine build
binds (`carTpages[city][8]` on the stock build). It only changes the
`clut-dump.txt` the bake writes next to `pallet.bin`. A set past that budget
still draws row 0 — the first page's palette — until the engine has a slot
for it. Raising the number does not invent rows.

`declare.auto_textures` (default `false`) is the opt-in safety net for wheel
sprites. With it off, every explicit `island` is used exactly as written and a
wheel that overlaps the page's body art or another car's sprite is refused by
the clash check. With it on, the bake keeps every island that still fits and
does not overlap, and moves only a wheel that cannot be used as declared: first
to the first free rect on its own page, then to another page the bank carries
(preferring the car's own pages). The relocation is reported with the old and
new rect. It does not invent space — if no page has a free rect the bake still
stops with the per-page inventory. This option relocates **wheel sprites only**;
body texture pages are whole 256x256 pages and are never moved or renumbered.

### Firebird (multi-id page + an image paint)

Stock materials are `set65_id0` for the body and `set65_id13` for the lamps.
`split: "auto"` keeps id 13 on its own CLUT row when the two panels do not fit
in one 16-entry row. A one-row page that already fits is left alone.

```json
"textures": {
  "65": {
    "png": "art/firebird.png",
    "split": "auto",
    "protect": ["ebebe8", "4b0a08"],
    "paints": {
      "yellow": "e0c020",
      "night": { "image": "art/firebird_night.png" },
      "lamps": { "image": "art/firebird_lamps.png", "id": 13 }
    }
  }
}
```

### REDBUICK (one D1 car, PAGE_N or set/id)

The OBJ is in Driver 1 units. Bake it at `2048/3000` (`carbank_bake --scale
0.68267`, the default when `declare.scale` is absent). Preview then places the handling wheels with that same
scale, and `preview.py` / `textured.py` / `spin.py` frame every car against
the Driver 2 median (length 752) so an unscaled mesh (~1104) draws larger
than the car the game shows.

A Driver 2 export, retail or the Aug 2000 demo, is already in engine units.
`exportd2` writes `declare.scale: 1` and `declare.donor.obj_scale: 1`. Measured
on HAVANA clean models, demo length / retail length is 1 (slot 0 both 750,
slot 10 both 1386; both map headers are cell size 2048). Baking that OBJ at
`2048/3000` is what makes it too small. `carbank_bake` with no `--scale` uses
`declare.scale` when the bank names one, so a demo bank is not silently given
the Driver 1 ratio. `donor.source` is `driver2-demo` or `driver2`; the bake
does not read it, and it is not a `cities.ini` key.

```json
"textures": {
  "1": { "png": "textures/PAGE_1.png", "split": "auto" }
}
```

Materials may be `PAGE_1` (the whole of set 1, id 0) or `set1_id0`. Both are
read. `split: "auto"` cuts UV islands that do not share a 16-entry row, and
paginates leftover sprites the mesh does not cover (other retail details still
on the page) onto their own CLUT rows so they do not crush the body. The
retail GSX mesh is not in this repo.

Civilian cars pick a column at random. The player car uses the frontend palette
(SQUARE on the car select cycles "Palette n/6"); a column nothing filled falls
back to column 0.

`atlas` is still there as the one-image shorthand for the single-page case: it
covers the sets the models name.

### The palette must match the pages

`LUMP_PALLET` is what paints a car's texel indices, and a page is quantised
against **its own row 0**. Carrying the donor city's rows leaves every index
painted by a colour chosen for different art, which reads as stray magenta or a
wrong grey on a panel. Build the rows from the pages:

    python carbank_palette.py bank.json --from-pages

`carbank_bake --check` refuses a bank whose palette disagrees with its pages, and
`carbank_palette.py bank.json` (without `--from-pages`) writes `pallet.bin` from
`palette.json`, so an edited colour reaches the bank by the same path the
donor's rows did.

### `wheels`

A Driver 2 car does not carry its wheels: the level has one shared
`CLEANWHEEL`/`WHEEL`/`FASTWHEEL` pair of texture details and the engine draws
every car with the host mesh. An imported car gets its own wheel by carrying a
wheel MODEL in the bank - a `LUMP_CAR_WHEELS` segment (type 200), one record per
variant, which `models.c BuildCarImportWheels` reads back. The engine still
keeps the host MESH; the record is where it gets the page and the crop rect to
draw it with.

That is why a wheel needs more than an image:

| key | required | meaning |
| --- | --- | --- |
| `set` | for a wheel model | the texture page the art lives on. A wheel record names a page, so a variant with no `set` cannot be carried and keeps the host level's wheel texture |
| `island` | unless `png` can supply it | the crop rect on that page, `[u0, v0, u1, v1]` in texels, `u1`/`v1` **exclusive** - the same rectangle PIL's `crop()` takes, so a 60x60 sprite at the page origin is `[0, 0, 60, 60]`. Left out, the bake uses the image's own size at the origin |
| `png` | **yes for a wheel that carries art** | the sprite. It is composited INTO its island on the page at bake time (see below), not merely measured |

The whole entry may also be a plain path (`"clean": "source/wheelclean.png"`),
which is an image with no page: valid, and it bakes no wheel record.

A bank carries **one** wheel set for the whole import - the segment has three
variants, not three per model slot - so the first car in slot order that declares
wheels owns it, and another car's wheels are reported rather than silently
merged.

**Declare all three variants.** `clean`, `damaged`, `fast` - an undeclared
variant is written as offset -1, which means "keep the HOST level's wheel", and
the host's is a retail sprite baked opaque: a pink square around a damaged car's
wheel. Reusing one sprite for `damaged` is a legitimate placeholder
(`"damaged": {"png": "textures/wheelclean.png", "set": 2, "island": [0, 0, 60, 60]}`).

#### The island is carved from the sprite

The page's own pixels at the island are **replaced** by the declared sprite, and
the sprite's own see-through texels (its alpha channel if it has one, else its
top-left colour) are masked transparent. So:

- whatever the page art carried at that rectangle cannot reach the bank;
- the declared sprite is the only thing that can end up in the wheel;
- editing the sprite re-bakes its page - it is part of the page's source hash.

`"island": "auto"` asks the bake to choose the rect for that one sprite.
`declare.auto_textures` is the bank-wide variant: it keeps a declared rect that
already fits and moves only a sprite that cannot be used as written (overlapping
body art or another car's sprite). Both forms relocate wheel sprites only; they
never move or renumber a body texture page.

The page still has to key its backdrop, because the engine has no per-texel alpha:
the marker is a CLUT entry of `0x0000` and `CarImportDeriveWheelPaletteRow` keys
it off the sprite's **corner index**. Declare the key on the page:

```json
"textures": {
  "2": { "png": "textures/page2.png", "transparent": "auto",
         "transparent_chroma": 128 }
}
```

`transparent` takes a hex colour (`ff00ff`), `auto` (the image's top-left pixel)
or `alpha`; `transparent_tolerance` widens the match and **defaults to 96** when a
key is given (`0` = the exact colour only, which leaves the antialiased ring round
a sprite painted). `carbank_bake` warns, naming the page and the indices, if any
palette entry still paints the key after keying.

A declared page or sprite may not live under `work/` or `pages/` - see the note
above the `textures` section - and the engine nowadays logs which row it derived
(`cross-city: ... wheel tyre sprite (0,0)-(60,60) background index 14 -> row 0`)
and warns instead of returning -1 when no row clears the backdrop.

### `sounds`

Per car, in the same shape for every model slot the bank carries:

| key | required | meaning |
| --- | --- | --- |
| `engine` | yes when `sounds` is present | the engine loop, WAV |
| `idle` | no | the idle loop; absent means the engine sample doubles as the idle, which is what retail Driver 1 banks do |
| `horn` | yes unless `cop` | the civilian horn |
| `siren` | yes when `cop` | the police siren |
| `cop` | no (default false) | this car is a police vehicle |
| `engine_pct` / `idle_pct` | no (100) | the source rate as a percentage, the Driver 1 beta knob |
| `loop_from` | no (0) | seconds into the looping samples where the loop starts |
| `peak` | no (0.999) | block-wide peak **ceiling** for every sample |
| `rms` | no (0.34) | block-wide **loudness** target (retail measures 29-39%); `0` means peak only |
| `max_rate` | no (16000) | block-wide rate ceiling |
| `sample_bytes` | no (16384) | per-sample SPU byte budget |

Each of `engine` / `idle` / `horn` / `siren` is either a path or an object with
that path as `wav` plus its own settings:

```json
"engine": { "wav": "sounds/engine.wav", "level": 0.999, "rms": 0.34,
            "normalise": true, "force": false, "gain_db": 0.0,
            "rate": null, "rate_pct": 100, "pitch": 2.0,
            "loop": "auto", "loop_from": 0.0, "loop_to": null,
            "trim": null, "fade_out": 0.0 }
```

`level` (peak ceiling), `rms` (loudness target; `0` = peak only),
`normalise` (false = keep the source level), `pitch` (scales the rate field
only, for a sample the game pitches down as it plays it), `loop`
(`true`/`false`/`"auto"`), `loop_to`,
`force` (rescale even when already at level), `gain_db`, `rate` (an exact
frequency, which wins over `rate_pct`), `rate_pct`, `loop`, `loop_from`,
`trim` (`[start, end]` seconds) and `fade_out` (seconds). The defaults are the
values above, and `docs/SOUNDS.md` explains what each one does and the order
they are applied in.

**A bare path is complete.** Everything above has a default that the bake fills
in, so `"engine": "sounds/engine.wav"` is a legal, finished declaration.
`carbank_bake --complete` writes those defaults into the file instead, which
changes nothing about the bake (`test_sound_knobs.py` asserts a completed
declaration bakes byte for byte the same) and lets someone read - and adjust -
what they are getting.

The samples are converted to PSX VAG/ADPCM at bake time (see
`docs/SOUNDS.md`), and the engine reads them as a three-sample bank: **engine,
idle, then the third sample**. A `cop` car puts the **siren** in the third slot
and leaves `horn` unused - the siren replaces the horn rather than joining it,
because that is the slot the game presses for a police car. Declaring both on a
cop car is refused: it hides which one would actually play.

`engine_pct` / `idle_pct` scale the source rate the way the Driver 1 beta knobs
do (`add_car_sounds.BETA_ENGINE_PITCH` = 60, `BETA_IDLE_PITCH` = 75). The result
is capped at `max_rate`, and then at what the SPU can play.

The bake writes the samples as `LUMP_CAR_SOUNDS` (201) - a per-model D2 dynamic
bank - and `cop` also sets `LUMP_CAR_SIRENS` (202). Both are verified the way
`CarImportSoundEntry` reads them (count exactly 3, offsets and payloads inside
the segment), because that reader answers a bad bank with "no sounds" and the car
then plays the host city's engine without a word.

`declare.police_voices` is optional: exactly 17 WAV paths, in phrase order (the 6 directions, then the 11 chatter lines). The bake writes them as `LUMP_POLICE_VOICES` (203), a complete replacement for the host city's cop phrase bank. `register_city.py --force-slot MODEL` points `carhacks.ini` at that bank's cop slot (`force_slot = 3:<city>:<MODEL>`).

Byte budget: `sample_bytes` per sample (16 KiB) at `max_rate` (16000 Hz). This is
not taste - `LoadSoundBankDynamic` writes the payload to a running SPU address
with no bounds check, so an oversized bank writes past the end of SPU RAM and
takes the game with it. Retail spends what it needs - its own three-sample car
bank measures 29,008 bytes at 16 kHz - so the cap is a ceiling, not a target. A longer loop is trimmed and the bake says so.

### `handling.driveWheels`

Optional, in a car's `handling` block: `"front"`, `"rear"` or `"all"`. Any other
value is a `BankError`. Leave it out and the car keeps the stock drive of the
slot it loads into. In stock that depends on the controller, not the model:
the rear wheels always take thrust, and the front wheels also take it under
`CONTROL_TYPE_PURSUER_AI` (`wheelforces.c` StepOneCar, the `(i & 1)` rear/front
split). Driver 1 cars are rear-wheel drive.

When at least one car sets it, the bake writes `LUMP_CAR_DRIVE` (205): 13 bytes,
one per model slot, then 3 zero bytes of padding. `0` = no override, `1` = front,
`2` = rear, `3` = all (bit 0 = front axle driven, bit 1 = rear). When no car
sets it the segment is left out, so the bank bakes byte-identical to one baked
before the field existed. `verify_drive` checks the segment against the
declaration. The engine has to read 205 itself (models.c, next to
`CAR_IMPORT_LUMP_SIRENS`); an engine that predates it skips the segment by its
size and keeps stock drive.

### `handling.policeLights`

Optional, in a car's `handling` block. How the car's police light bar looks. A
car that leaves it out keeps the engine's default: the light bar of the car's
source city, which for any bank city (not Driver 2's own four) is the generic
two-lamp red/blue bar. Give exactly one of `style` or `lights`.

**Wear a Driver 2 city's bar** - `style`:

```json
"policeLights": { "style": "HAVANA" }
```

`CHICAGO` (two red lamps, both sides), `HAVANA` and `RIO` (one blue lamp, one
side, slow), `VEGAS` (three sliding red/blue lamps), or `HOST`: whichever city
the level is running, which is what a level's own cop cars do. The bar is drawn
from the car's `policeLight` anchor, exactly as in that city. Exporting a Driver
2 city with `exportd2` writes this for the police car (model 0) from the city it
came from, so a rebaked Havana cop keeps its one blue lamp; an existing
`policeLights` is never overwritten.

**Place every lamp yourself** - `lights` (1..8 lamps):

```json
"policeLights": {
  "speed": 16,
  "lights": [
    { "name": "front L", "at": [-60, -135, -300], "color": "blue" },
    { "name": "front R", "at": [ 60, -135, -300], "color": "blue", "phase": 128 },
    { "name": "rear",    "pos": [0, -135, 250], "color": "amber", "mirror": true, "size": 60 }
  ]
}
```

| key | meaning |
| --- | --- |
| `at` | the lamp's point **in the model**, in the car's OBJ coordinates (what you see in Blender). Up is negative y, as in the OBJ. Nothing is converted and the COG plays no part: the engine draws the body at `cog + vertex` and a light at `cog + position`, so a light's vector *is* its OBJ point. (Stock cop cars confirm it: their `policeLight` sits about 25 units under the OBJ's roof.) |
| `pos` | the same vector written the way `handling.policeLight` is. Identical numbers to `at`; give exactly one of `at` / `pos`. |
| `color` | `red` (default), `blue`, `amber`, `white`, or `[r, g, b]` 0..255. The presets keep the stock bar's off-channels at 90, so red is `[255, 90, 90]`; at night every colour drops its weaker channels the way the stock bar does. |
| `phase` | 0..255, where the lamp is in the flash cycle (default 0). `128` flashes exactly opposite `0`: that is what makes a pair alternate. |
| `mirror` | `true` also draws the lamp at `-x`, so one entry makes a left/right pair. |
| `size` | percent of a stock lamp, 1..255 (default 100). A small beacon wants 40-60. |
| `speed` | (top level) flash cycle units per frame, 1..255 (default 16, about two flashes a second). Every lamp flashes at this rate, however many there are. |
| `name` | a label for you; the bake ignores it. |

A lamp is lit for half of its cycle and ramps up, as the stock bar's lamps do.
The `super mini cars` cheat scales the lamp positions with the rest of the car.

The bake writes `LUMP_CAR_LIGHTS` (206): a version word, the model count (13),
13 offsets (0 = none), then one block per car that sets it - `mode` (1 style,
2 custom), `style`, `count`, `speed`, and per lamp `i16 x, y, z` then `u8 r, g,
b, phase, flags, size`. When no car sets it the segment is left out, so a bank
that never mentions it bakes byte-identical. `verify_lights` checks the segment
against the declaration. The engine reads it in `models.c`
(`CAR_IMPORT_LUMP_LIGHTS`, `JerCarSlotCopLights`); an engine that predates it
skips the segment by its size and keeps the default bar. A bad block is dropped
with a warning in the log and that car keeps the default bar.

#### Finding where a lamp goes: `copmarkers.py`

Working the numbers out by hand is slow (the engine's axes are not Blender's, and a
wrong sign puts the lamp inside the car). Put the lamp in the model instead:

1. In Blender, put the 3D cursor on the beacon, add a small object there (a cube,
   a plane or a single vertex) and name it `COPLIGHT_<words>`, for example
   `COPLIGHT_L_blue`, `COPLIGHT_R_blue_p128`, `COPLIGHT_rear_amber_m_s60`. The words
   after `COPLIGHT` set the lamp: a colour name, `p<n>` phase, `m`/`mirror`,
   `s<n>` size percent; anything else is just the label. (`blender_add_coplight.py`
   adds one at the cursor.)
2. Export the OBJ the way you always do. The marker is exported with the car, so it
   is in the car's own coordinates; nothing is converted by guesswork.
3. `python copmarkers.py cars/<car>/handling.json` lists the lamps it found, in
   the model's coordinates. Add `--write` to store them in `handling.json`
   (`--replace-style` to swap out a `style`). It keeps the file's line endings
   and every other key.
4. Rebake. The bake drops marker objects from the car's geometry, so they never
   show in the game.

`--scale` defaults to the bank's `declare.scale`, as the bake reads it, and
`--flip-y` / `--flip-z` must match the rebake mode you use (`flipy`, `full`);
the tool prints the scale and where it came from. Markers are read from the
`clean` OBJ (`--variant damaged|low` for another).

### `handling.policeVoice`

Whose police radio lines a cop car speaks with. One bank can mix cars from every
city (COPSNRBRS does), and each keeps its own city's radio:

```json
"policeVoice": "HAVANA"
```

| Value | Lines |
| --- | --- |
| `CHICAGO`, `HAVANA`, `VEGAS`, `RIO` | that Driver 2 city's stock radio lines |
| `HOST` | the level's own lines |
| `OWN`, or the key absent | the source-city rule: the bank's `declare.police_voices`, else Driver 1 Miami's, else the host's |

The bake writes `LUMP_CAR_VOICE` (207), one byte per model slot, only when some
car sets it; the report lists it under `police_voice`. Each extra city costs 17
voice samples and their sound memory; when either runs out the car keeps the
host's lines (the game log says so).

### Damage tables

Every bake writes `LUMP_CAR_DENTING` (208): each model's own damage zones and
per-poly damage levels, in the `.DEN` block layout (`dentgen.py`). Its poly
indices are the engine's (from the baked model), so the right polys shift for
every car.

Zones split the car by side (x) and front/middle/rear (z, front is −z), the same
way a collision hit picks one (bcollide.c). The art at the shifted positions joins
the material's palette row (`dent_shadows`).

#### `handling.damage`

Optional, in a car's `handling` block. Controls how `dentgen.py` decides the UV
shift for each body poly when the car is dented.

| value | behaviour |
|-------|----------|
| `"auto"` (default, key absent) | probe the baked page for each poly. A shift is only used when enough of the poly's texels land on drawable art that no other poly already occupies, so a page carrying damage strips at +64/+128 gets the vanilla progression and a poly with nothing beside it keeps its texture when hit. |
| `{"stock": "HAVANA", "model": 2}` | the car **is** a stock Driver 2 car, repackaged (a cop car with a light bar, an extra panel). The bake reads `LEVELS/<CITY>.DEN` and `<CITY>.LEV` from the game folder (found by walking up from the bank, or `JERICHO_GAME`), matches every baked vertex and poly to the stock one at the same position, and copies the stock level byte and zone membership for each. That is the original game's own damage progression, poly for poly. Parts the stock car does not have never shift texture and dent with the zone their position puts them in. Falls back to `"auto"` (with a warning) when the files are not found or fewer than 70% of the stock car's polys are in the baked one. |
| `"d1"` | every textured denting poly gets D=1: **+64 px at light damage, +128 px at heavy**, whether or not there is art there. The vanilla D1/D2 *assumption* rather than a measurement; the bank author is then responsible for the art at those offsets. Use it when a page does carry the strips but the probe cannot see them (art the palette row draws as `0x0000`, or a strip another poly also covers). |

`"d1"` was the default up to this release. On a custom page without damage strips it shifted **every** panel 64 texels sideways into whatever art happened to sit there, which is the "the damage UVs are wrong instead of following the original progression" report. The probe is the safer default: at worst a panel does not change texture, where the other way round wrecks the paintwork. Stock Driver 2 cars mostly use D=0 - nothing at light damage, +128 at heavy - with D=1 on a few panels, so neither mode reproduces them; that is what the `stock` form is for. A re-bake prints the per-car counts (`shift_64_128`, `shift_128`, `shift_64`, `still`; for `stock`, `matched_polys` / `new_polys`), so you can see which polys the probe left alone and reach for `"d1"` if your page really does carry strips everywhere.

Example:

```json
"handling": { "damage": "auto" }
```

The report's `damage` block counts the polys per behaviour for each model
(`shift_64_128`, `shift_128`, `shift_64`, `still`). A car without the segment
(an older bake) is left undented in game rather than using the host city's table.

### `remap`

`set` and `id` are objects keyed by the source value as a string,
`{ "source": target }`. Anything not listed is left alone, so a bank that renumbers
one page does not have to restate the rest.

## Validation

`bankjson.load(path)` is the only reader. It fails hard, naming the file and the
field — never a warning and never a guess. It checks the schema version, the
slot range and uniqueness, that every named file exists, that `atlas` and
`pages` are not both absent, that a cop car declares `siren` and not `horn`,
that a non-cop car declares `horn`, and that no unknown key appears (a typo in a
key would otherwise silently be ignored).
`declare.donor` is a label only (`city`, `model`, `slot`); an old `lev` is
accepted and ignored.

`tools/car/test_bankjson.py` holds the cases.

## Preview

`preview_car.py <bank.json|dir>` renders `declare.cars.<car>` authoring art (clean OBJ+MTL, wheel PNGs, engine/idle WAVs) with a slow yaw and an idle -> gears -> damaged-wheel coast loop. Options: `--car`, `--timeout`, `--stop-file`, `--mute`, `--flip-y` (Y-down art), `--shot DIR`. Copy the Firebird `rebake.bat` preview block (`STOPF` + `start /b python preview_car.py`) into other banks' rebake scripts.


## Material names

Name each Blender material after the texture page it paints on: `PAGE_10` (also `PAGE_10.png`, `PAGE_10.001`) means the **whole** of page 10, baked as set 10 id 0. You never write set/id yourself. If `textures` has no `10` entry, the bake wires in `textures/PAGE_10.png` from the car folder; with no such file it stops and tells you to add `textures.10`.

Stock Driver 2 exports keep `set<S>_id<T>` (optional `_ft` for flat-textured) and `flat` (untextured). `--default-page set:id` still maps any other name.

