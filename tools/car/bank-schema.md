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

Optional. Put `portrait` on the car in `cars/<id>/handling.json`. That is the author-facing field. An inline car object in `bank.json` accepts the same key and goes through the same packer; prefer the handling file, which is where the rest of that car already lives.

The value is a path, relative to the handling file (or to `bank.json` when the car object is inline). A source under `work/`, `pages/` or `build/` is refused, same as a texture.

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

#### Paints (colour variants)

    "1": { "png": "art/textures/firebird.png",
           "protect": ["ebebe8", "4b0a08"],
           "paints": { "yellow": "e0c020", "red": "c02020",
                       "green": "20a040", "grey": "909090" } }

The engine keeps six colour columns per texture (`civ_clut[row][tex][0..5]`).
Column 0 is ALWAYS the page's own colours - the original paint. Each declared
paint becomes one more palette record (lump `palette` k -> column k+1), so the
example above gives 0 blue, 1 yellow, 2 red, 3 green, 4 grey.

A hex paint copies the page's 16-colour row and recolours only the BODY entries:
hue and saturation from the paint, shading kept (the brightest body entry lands
on the paint colour's own lightness). Never touched: `0x0000` (transparent),
`protect` colours (lights), and anything that is not body - by default the
dark/near-grey entries (glass, trim, tyres). If glass is a light blue and gets
painted, list the body colours explicitly in `paint_body`.

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

`declare.clut_rows` (default 8) is how many `civ_clut` rows this engine build
binds (`carTpages[city][8]` on the stock build). It only changes the
`clut-dump.txt` the bake writes next to `pallet.bin`. A set past that budget
still draws row 0 — the first page's palette — until the engine has a slot
for it. Raising the number does not invent rows.

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
0.68267`, the default). Preview then places the handling wheels with that same
scale, and `preview.py` / `textured.py` / `spin.py` frame every car against
the Driver 2 median (length 752) so an unscaled mesh (~1104) draws larger
than the car the game shows.

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

Byte budget: `sample_bytes` per sample (16 KiB) at `max_rate` (16000 Hz). This is
not taste - `LoadSoundBankDynamic` writes the payload to a running SPU address
with no bounds check, so an oversized bank writes past the end of SPU RAM and
takes the game with it. Retail spends what it needs - its own three-sample car
bank measures 29,008 bytes at 16 kHz - so the cap is a ceiling, not a target. A longer loop is trimmed and the bake says so.

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

