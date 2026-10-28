# bank.json — what a person writes, and what the bake computes

A bank folder holds one car bank: `FIREBIRD.LEV` + `FIREBIRD.LCF` (the loadable
pair), the `raw/` blocks it was built from, and `bank.json`. That one file has
two halves:

| half | who writes it | what it holds |
| --- | --- | --- |
| top level (`city`, `lev`, `lcf`, `size`, `included_sets`, `pages`, `palette_records`, `page_base`, `warnings`, `checks`) | the bake, every time | what the bank turned out to be |
| `declare` | the author, once | what the author wants: sources, layout, sounds |

**The bake never overwrites `declare`.** It is read, validated, used, and written
back untouched — including the files it points at, so a rebuild that fails cannot
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
          "clean":   "source/wheelclean.png",
          "damaged": null,
          "fast":    "source/wheelfast.png"
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
| `pages[]` | one of | `{set, png}` per texture page — use this instead of `atlas` when the pages are separate images |
| `wheels.clean` | no | the road wheel; absent means the donor's wheel texture |
| `wheels.damaged` | no | the buckled wheel — **omit it cleanly** when the car has none; the engine then keeps the host wheel texture for that variant (`models.c:2743`) |
| `wheels.fast` | no | the high-speed blur wheel |
| `sounds` | no | see below |
| `palette` | no | a CLUT source for a car whose colours are not the donor's |
| `remap` | no | `set` / `id` maps applied to every poly as it is read |

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
and leaves `horn` unused — the siren replaces the horn rather than joining it,
because that is the slot the game presses for a police car. Declaring both on a
cop car is refused: it hides which one would actually play.

Rate: each sample keeps its own file's rate unless `sounds.engine_pct` /
`sounds.idle_pct` restate it, in the same percent-of-source-rate sense as the
Driver 1 beta knobs (`add_car_sounds.BETA_ENGINE_PITCH` = 60,
`BETA_IDLE_PITCH` = 75). Rates are clamped to what the SPU can play.

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
