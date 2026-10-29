# bank.json â€” what a person writes, and what the bake computes

A bank folder holds one car bank: `FIREBIRD.LEV` + `FIREBIRD.LCF` (the loadable
pair), the `raw/` blocks it was built from, and `bank.json`. That one file has
two halves:

| half | who writes it | what it holds |
| --- | --- | --- |
| top level (`city`, `lev`, `lcf`, `size`, `included_sets`, `pages`, `wheels`, `palette_records`, `page_base`, `warnings`, `checks`) | the bake, every time | what the bank turned out to be |
| `declare` | the author, once | what the author wants: sources, layout, sounds |

**The bake never overwrites `declare`.** It is read, validated, used, and written
back untouched â€” including the files it points at, so a rebuild that fails cannot
leave the declaration half-renamed. A bank with no `declare` block is a
donor-derived bank (what `basebank.py` builds) and is used as it stands.

## Schema

```json
{
  "city": "FIREBIRD",
  "lev": "FIREBIRD.LEV",
  "lcf": "FIREBIRD.LCF",

  "declare": {
    "schema": 1,
    "name": "FIREBIRD",

    "donor": { "city": "CHICAGO", "model": 1, "slot": 1, "lev": null },

    "order": [
      { "slot": 1, "car": "FIREBIRD_model1" }
    ],

    "cars": {
      "FIREBIRD_model1": {
        "clean":   "source/Firebird_high.obj",
        "damaged": null,
        "low":     "source/Firebird_low.obj",

        "atlas":   "source/firebird.png",
        "pages":   [ { "set": 1, "png": "source/page1.png" } ],

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

Every path is relative to the folder holding `bank.json`.

### `order[]`

The bank's model slots, in engine order: `slot` is 0..12 and unique, `car` names
an entry in `cars`. A slot with no entry in `cars` keeps the donor's blocks.
Slots the bank does not carry must be left out rather than set to `null` â€” the
bake writes `-1` for them.

### `cars.<name>`

| key | required | meaning |
| --- | --- | --- |
| `clean` | yes | the undamaged body, OBJ |
| `damaged` | no | the crumpled body; absent means the donor's |
| `low` | no | the low-detail body (LOD); absent means the donor's |
| `atlas` | one of | a single atlas PNG covering every page, in page order |
| `pages[]` | one of | `{set, png}` per texture page â€” use this instead of `atlas` when the pages are separate images |
| `wheels.clean` | no | the road wheel: `{png, set, island}` â€” see below |
| `wheels.damaged` | no | the buckled wheel â€” **omit it cleanly** when the car has none; the bank writes `-1` and the engine keeps the host wheel texture for that variant (`models.c:2743`) |
| `wheels.fast` | no | the high-speed blur wheel |
| `sounds` | no | see below |
| `palette` | no | a CLUT source for a car whose colours are not the donor's |
| `remap` | no | `set` / `id` maps applied to every poly as it is read |

### `pages`

A page entry names its image **and** how that image is baked. The second half is
what makes a refresh possible: the bank bake re-bakes a page whose source, or
whose bake options, no longer match what it recorded last time, and an unchanged
declaration still produces identical bytes.

| key | required | meaning |
| --- | --- | --- |
| `set` | yes | the texture page id |
| `png` | yes | the image. On its own this is a **preview** — the bake never re-derives a page from it, because a preview round trip is generation loss |
| `source` | no | the image the page is BAKED from. Declaring it opts the page into the refresh: when this file or any option below changes, the bake re-bakes it and records the image's sha256 |
| `transparent` | no | the backdrop colour to make transparent: a hex colour (`ff00ff`), `auto` (the image's top-left pixel) or `alpha` (the PNG's alpha channel). There is no per-texel alpha in this format — the engine's marker is a CLUT entry of `0x0000` — so a sprite on a flat backdrop is an opaque square without this |
| `transparent_tolerance` | no | per-channel slack for that match (0..255). A soft, anti-aliased edge needs some |
| `protect` | no | colours the page's 16 MUST keep (`["b8b8c0", ...]`). The quantiser evicts the least-used colour instead, which is how a headlight grey survives |
| `palette` | no | exactly 16 hex colours: the page is re-indexed to them and nothing is quantised |

```json
"pages": [
  { "set": 1, "png": "work/pages/page1.png", "source": "textures/firebird.png" },
  { "set": 2, "png": "work/pages/page2.png", "source": "work/pages/page2.png",
    "transparent": "auto", "transparent_tolerance": 96 }
]
```

`carbank_bake.py <bank.json> --check` says which pages are stale, and
`--audit RRGGBB` says where a colour still lives (page, texel count, palette
index, CLUT word, transparent or not) — the two questions "my new art did not
take" and "there is still magenta somewhere" actually ask.

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
wheel MODEL in the bank â€” a `LUMP_CAR_WHEELS` segment (type 200), one record per
variant, which `models.c BuildCarImportWheels` reads back. The engine still
keeps the host MESH; the record is where it gets the page and the crop rect to
draw it with.

That is why a wheel needs more than an image:

| key | required | meaning |
| --- | --- | --- |
| `set` | for a wheel model | the texture page the art lives on. A wheel record names a page, so a variant with no `set` cannot be carried and keeps the host level's wheel texture |
| `island` | unless `png` can supply it | the crop rect on that page, `[u0, v0, u1, v1]` in texels, `u1`/`v1` **exclusive** â€” the same rectangle PIL's `crop()` takes, so a 60x60 sprite at the page origin is `[0, 0, 60, 60]`. Left out, the bake uses the image's own size at the origin |
| `png` | no | the art. Used to measure a default `island`; a declaration read back out of a bank names the rect and has no image file |

The whole entry may also be a plain path (`"clean": "source/wheelclean.png"`),
which is an image with no page: valid, and it bakes no wheel record.

A bank carries **one** wheel set for the whole import â€” the segment has three
variants, not three per model slot â€” so the first car in slot order that declares
wheels owns it, and another car's wheels are reported rather than silently
merged.

The art's backdrop has to be **transparent**, and there is no per-texel alpha
here: the engine's marker is a CLUT entry of `0x0000`, and it keys the backdrop
off the sprite's corner index. A sprite baked on a pink sky therefore draws as an
opaque square â€” bake the page with the colour key:

    python carbank_page.py work/pages/page2.png --set 2 --bank . --transparent auto

`--transparent` takes a hex colour (`ff00ff`), `auto` (the image's top-left
pixel) or `alpha` (the PNG's own alpha channel); `--transparent-tolerance` widens
the match for a soft edge. `carbank_bake` warns, naming the page and colour, when
a wheel's backdrop is still opaque.

### `sounds`

Per car, in the same shape for every model slot the bank carries:

| key | required | meaning |
| --- | --- | --- |
| `engine` | yes when `sounds` is present | the engine loop, WAV |
| `idle` | no | the idle loop; absent means the engine sample doubles as the idle, which is what retail Driver 1 banks do |
| `horn` | yes unless `cop` | the civilian horn |
| `siren` | yes when `cop` | the police siren |
| `cop` | no (default false) | this car is a police vehicle |

The samples are converted to PSX VAG/ADPCM at bake time (see
`docs/SOUNDS.md`), and the engine reads them as a three-sample bank: **engine,
idle, then the third sample**. A `cop` car puts the **siren** in the third slot
and leaves `horn` unused â€” the siren replaces the horn rather than joining it,
because that is the slot the game presses for a police car. Declaring both on a
cop car is refused: it hides which one would actually play.

Rate: each sample keeps its own file's rate unless `sounds.engine_pct` /
`sounds.idle_pct` restate it, in the same percent-of-source-rate sense as the
Driver 1 beta knobs (`add_car_sounds.BETA_ENGINE_PITCH` = 60,
`BETA_IDLE_PITCH` = 75). Rates are clamped to what the SPU can play.

The bake writes the samples as `LUMP_CAR_SOUNDS` (201) — a per-model D2 dynamic
bank — and `cop` also sets `LUMP_CAR_SIRENS` (202). Both are verified the way
`CarImportSoundEntry` reads them (count exactly 3, offsets and payloads inside
the segment), because that reader answers a bad bank with "no sounds" and the car
then plays the host city's engine without a word.

Byte budget: **8 KiB per sample**, and the rate is capped at 11025 Hz. This is
not taste — `LoadSoundBankDynamic` writes the payload to a running SPU address
with no bounds check, so an oversized bank writes past the end of SPU RAM and
takes the game with it. Retail car banks are 2-4 KiB per car for the same
reason. A longer loop is trimmed and the bake says so.

### `remap`

`set` and `id` are objects keyed by the source value as a string,
`{ "source": target }`. Anything not listed is left alone, so a bank that renumbers
one page does not have to restate the rest.

## Validation

`bankjson.load(path)` is the only reader. It fails hard, naming the file and the
field â€” never a warning and never a guess. It checks the schema version, the
slot range and uniqueness, that every named file exists, that `atlas` and
`pages` are not both absent, that a cop car declares `siren` and not `horn`,
that a non-cop car declares `horn`, and that no unknown key appears (a typo in a
key would otherwise silently mean "use the donor's").

`tools/car/test_bankjson.py` holds the cases.
