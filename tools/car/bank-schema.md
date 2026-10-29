# bank.json — what a person writes, and what the bake computes

A bank folder holds one car bank: `FIREBIRD.LEV` + `FIREBIRD.LCF` (the loadable
pair, installed by the bake), the author's sources, and `bank.json`.

**`bank.json` is input only.** It holds `city` / `lev` / `lcf` (what the bank is
called), the optional layout keys below, and `declare` (what the author wants:
sources, layout, sounds). The bake reads and validates it and never writes it.
What the bank turned out to be - size, included sets, pages, palette records,
checks, warnings - goes to `<CITY>.report.json` beside the built manifest.
(Older banks still carry that computed half at the top level of `bank.json`;
it is ignored and can be deleted.) A bank with no `declare` block is a
donor-derived bank (what `basebank.py` builds) and is used as it stands.

## Folder layout

Two optional top-level keys, both relative to the bank folder, split inputs
from outputs:

```json
{ "city": "FIREBIRD", "lev": "FIREBIRD.LEV", "lcf": "FIREBIRD.LCF",
  "donor_dir": "donor", "build_dir": "build", "declare": { ... } }
```

| folder | who writes it | holds |
| --- | --- | --- |
| bank folder | the author (+ the installed LEV/LCF) | `bank.json`, `FIREBIRD.LEV/.LCF`, `cities.ini`, scripts, README |
| `art/`, `sounds/` (any path the declare names) | the author | OBJ/MTL, page and wheel PNGs, the three WAVs |
| `donor_dir` | `basebank.py`, once | `<CITY>.json` (the donor manifest, paths relative to it), `source.LCF`, `cosmetics.json`, `textureinfo/texturenames/modelnames/pallet.bin`, `palette.json`, `<CITY>.donor.LEV` |
| `build_dir` | the bake, every time | `pages/`, `models/`, `pallet.bin` (palette_from_pages), `textureinfo.bin` (when page sizes change), the built `<CITY>.json`, `<CITY>.report.json`, and the LEV/LCF when `-o` is not given |

The bake starts from the built manifest when `build_dir` has one (it remembers
which pages are current) and from the donor manifest otherwise, so deleting
`build_dir` is always safe - the next bake re-derives everything. Handling still
comes from the donor's `source.LCF`.

Leave either key out and it is the bank folder itself: the flat layout every
bank used before, which still bakes exactly as it did (except that `bank.json`
is no longer rewritten).

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

Every path is relative to the folder holding `bank.json`.

### `order[]`

The bank's model slots, in engine order: `slot` is 0..12 and unique, `car` names
an entry in `cars`. A slot with no entry in `cars` keeps the donor's blocks.
Slots the bank does not carry must be left out rather than set to `null` — the
bake writes `-1` for them.

### `cars.<name>`

| key | required | meaning |
| --- | --- | --- |
| `clean` | yes | the undamaged body, OBJ |
| `damaged` | no | the crumpled body; absent means the donor's |
| `low` | no | the low-detail body (LOD); absent means the donor's |
| `atlas` | one of | a single atlas PNG covering every page, in page order |
| `textures` | one of | `{set id: image}` per texture page. Use this instead of `atlas` when the pages are separate images |
| `wheels.clean` | no | the road wheel: `{png, set, island}` — see below |
| `wheels.damaged` | no | the buckled wheel — **omit it cleanly** when the car has none; the bank writes `-1` and the engine keeps the host wheel texture for that variant (`models.c:2743`) |
| `wheels.fast` | no | the high-speed blur wheel |
| `sounds` | no | see below |
| `palette` | no | a CLUT source for a car whose colours are not the donor's |
| `remap` | no | `set` / `id` maps applied to every poly as it is read |

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
key would otherwise silently mean "use the donor's").

`tools/car/test_bankjson.py` holds the cases.
