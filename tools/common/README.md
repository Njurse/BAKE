# BAKE common helpers

Shared helpers used by both the car and ped suites. Keep suite-specific code in
`../car/` or `../ped/`.

## fourbpp.py — the 4bpp page and CLUT conversions

Driver 2 textures are 4bpp pages (8-bit indices, two per byte) with 16-entry
CLUT rows of RGB555 words, 32 bytes each. `fourbpp.py` is the single
implementation of that conversion:

| function | direction |
| --- | --- |
| `rgb555(rgb8)` | 8-bit `(r,g,b)` → one RGB555 word (STP clear) |
| `clut_rgb(row)` | 32-byte CLUT row → 16 8-bit `(r,g,b)` tuples |
| `rgb_to_clut(colors)` | up to 16 colours → one 32-byte CLUT row |
| `flat_clut_row(rgb8)` | a row whose 16 entries are all one colour |
| `CLUT_ROW_BYTES`, `CLUT_ROW_ENTRIES`, `CLUT_GROUP_BYTES` | 32 / 16 / 128 |

And the rest of the 4bpp surface, promoted out of the two suites:

| function | what it does |
| --- | --- |
| `decode_indices(raw)` / `encode_indices(indices)` | packed 4bpp page image ↔ one index byte per texel |
| `decomp_asm(src, out_bytes)` / `encode_literal(data)` | the engine's backwards page RLE and its literal-only encoder |
| `nearest_clut_index(rgb, clut)` | nearest index 0..15, never collapsing a non-black texel onto the transparent black entry |
| `reindex_to_clut(pixels, clut)` | RGB texels → indices against a page's existing palette |
| `paletteize(pixels, w, h)` | RGB texels → (16 colours, indices) when a crop brings new colours |
| `PAGE_W`, `PAGE_H`, `PAGE_BYTES`, `PAGE_INDEX_BYTES` | 256 / 256 / 65536 / 32768 |

Before this module the car tools (`bake._flat_555`, `bake._flat_clut_row`,
`pages1.clut_rgb`) and the ped tools (`jmpped_images.clut_rgb`,
`jmpped_images.rgb_to_clut`) each carried their own copy. Both copies rounded
identically — the same `>> 3` scaling in both directions — so they were merged
rather than one being chosen.

**Nothing here may change the bytes.** The moment a conversion rounds
differently, every existing car bank and pedestrian page shifts colour. The
merge was verified by rebuilding the FIREBIRD bank before and after: both
`FIREBIRD.LEV` (sha256 `e208bdb2…`) and `FIREBIRD.LCF` (`cab7b949…`) came out
byte-identical, and the ped suite's pass/fail set did not change.

Consumers add `tools/common` to `sys.path` and import `fourbpp`; the old names
stay as thin delegates so existing callers and tests keep working.
