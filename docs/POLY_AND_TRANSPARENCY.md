# Polygons, texture transparency, and the poly type byte

Canonical for the D1→D2 transplant. Everything here is measured in **this** repo and
carries `file:line`. Where the web and the code were compared, the code wins and any
disagreement is called out.

Written because a wheel-well cutout on a transplanted Driver 1 car renders as a **solid
square**, and the first three explanations for it were all wrong. Recording the mechanism
so the fourth is not needed.

---

## 1. Transparency is TWO things, and a car body has neither

PSX transparency is not a property of a polygon. It is the AND of:

| part | where it lives | what it does |
|---|---|---|
| **primitive semi-trans bit** | the GPU polygon command, `code & 2` | *enables blending at all* |
| **per-texel STP** | bit 15 of the CLUT entry a texel indexes | *how* the texel blends |

`PsyX_GPU.cpp:875` — `const bool semiTrans = (polyTag->code & 2);`
`PsyX_GPU.cpp:677` — `BlendMode blendMode = semiTrans ? GET_TPAGE_BLEND(tpage) : BM_NONE;`

**When the primitive bit is clear the texel is drawn opaque and STP is ignored entirely.**
The blend mode itself is tpage bits 5–6 (`PsyX_GPU.cpp:14`, `GET_TPAGE_BLEND = ((tpage>>5)&3)+1`).

Per-texel, STP becomes alpha: `PsyX_render.cpp:961` — `pixel[3] = ((c>>15)&1)<<7;` then
`PsyX_render.cpp:607/615` — `t.w = 1.0 - t.w;`. STP=1 gives alpha 0.5; STP=0 gives 1.0.

### The measured fact that explains the bug

**No car-BODY path in this engine sets the semi-trans bit.** The body plotters bake opaque
command bytes directly:

- `cars.c:495` (FT3) and `cars.c:644` (GT3) — `0x24000000` / `0x34000000`; command bit 1 is **0**
- `cars.c:457` (B3) likewise

The only `setSemiTrans` in the whole car file is `cars.c:1080`, inside `DrawWheelObject`.
Its argument is the global `TransparentObject`, which is

- defined `0` at `cars.c:422`
- assigned **only** `TransparentObject = 0;` at `cars.c:3096`
- read at `cars.c:1381` / `cars.c:1383`

Four references in the tree, and **it is never set to 1.** So even the wheels are drawn
opaque here.

There is also **no** code anywhere in the car path that compares a CLUT entry to `0x8000`
or tests its bit 15. Searches for `semitrans|0x8000|STP` reach font and CLUT *builders*
(`pres.c`, `texture.c:536` preserving STP when dyeing) — never a draw-time test.

**Consequence for the transplant.** A D1 body texel of `0x8000` is a valid PSX cutout *only
if its primitive carries the semi-trans bit*. This engine masks the type byte
(`& 0x1f`), stores no transparency in `CAR_POLY`, and resolves the unset bit to `BM_NONE` —
so the black texel is drawn **solid**. The square cannot be made transparent through any
existing path. It is not a bake bug and not a type-byte bug.

### The one working example

`DrawWheelObject` is the only place it is done — and it uses the **additive** trick rather
than alpha: `cars.c:1028` — `tpage = texture_pages[set] | 0x20`, forcing ABR=1, with
`setSemiTrans(..., TransparentObject)`. Under additive blend a **black+STP** corner texel
adds nothing, so the round wheel shows through. Our tools describe the same marker:
`pages1.py:404-414` ("0x8000 … the engine's transparent marker") and `textured.py:86-88`
("drawing them opaque punches black holes in the car") — but those are the **Python
previews**, not the engine.

### NOT verified

- Whether **stock Driver 2 bodies** contain any `0x8000` texels at all. No D2 model was
  decoded for this. Treat "D2's body has no 0x8000 texels" as *inferred*, not measured.
- Whether **original Driver 1** set a per-poly semi-trans bit that the data still carries.
  This repo has neither the field nor the code, so it cannot be answered from here.

---

## 2. The poly type byte: 5 bits of type, 3 bits of flags

The engine's convention is `type & 0x1f` for the type and sizes come from
`PolySizes[56]` (`draw.c:45`) — so three high bits are *available* as flags.

**Who reads them differs by caller, and that is the trap:**

| caller | indexing | effect |
|---|---|---|
| car path | `ptype & 0x1f` — `cars.c:1667`, `cars.c:1818` | high bits **discarded** |
| general mesh walkers | `PolySizes[ptype]` **unmasked** — `pedest.c:286`, `sky.c:928` | high bits select the **32..55 size bank** |

So the same high bits are a *size selector* elsewhere and are stripped for cars. They are
**not** a transparency flag in either case. `cars.c:1667` switches only on
`0,1,18,19,20,21,22,23` — texture class, nothing more.

**Types 24 and 25 do not exist:** `PolySizes[24] = PolySizes[25] = 0` (`draw.c:45-46`;
indices 20–23 are `20,20,24,24` and 24–31 are all zero). The earlier hypothesis that
22/23 are opaque and 24/25 transparent is therefore **impossible**, not merely unproven.

### The D1 id map, and what collapsing it costs us

`lev1.py:246-253`. The comment there is the important part: **the same kind is stored at
different sizes** — `F3` is 8 bytes as type 0 but 12 as type 16. That means the id
difference is *layout*, not decoration.

| kind | D1 ids that map to it |
|---|---|
| F3 | `0, 8, 16, 18` |
| F4 | `1, 17, 19` |
| FT3 | `4, 10, 20` |
| FT4 | `5, 7, 9, 11, 21` |
| GT3 | `22` |
| GT4 | `23` |

Our bake writes a **constant `22`/`23`** for every textured poly (`bake.py:97`, `bake.py:110`).
So whatever the D1 id carried beyond "which of six kinds is this" is **discarded on
conversion**. Whether anything lived in it is exactly what section 3 tests.

---

## 3. The open leads

**The mirrored grille** (user's lead). D1 cars appear to mirror the grille texture across
both sides, sharing one palette. If D1 mirrors by **reusing one texture with flipped U**,
then a grille poly and its mirror partner share a `texture_id` and have mirrored U values —
and a bake that normalises the flip leaves one side unmirrored, which on asymmetric grille
detail reads as "messed up on one side". This is a **UV/winding** question with a plausible
**no-engine-change fix**, so it is the cheapest open item. The test is direct: compare the
pair's UVs in the source, then in the blob, then against a mirrored pair on a D2 car.
