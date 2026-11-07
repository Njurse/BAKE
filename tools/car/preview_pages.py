#!/usr/bin/env python3
"""preview_pages.py - the pages the vehicle preview draws, POST-PROCESSED.

The preview must show what the game will draw, not the authored 8-bit PNG. For
every page set the car's materials name this module:

  * bakes the declared page through carbank_bake.bake_declared_page(), the same
    call the bank bake writes - 4bpp indices, one CLUT row per texture id, the
    declared wheel sprites composited into their islands;
  * applies the requested civ_clut paint column (--palette N, column 0 by
    default) through carbank_palette.paint_rows_for(), the same code that
    writes LUMP_PALLET;
  * returns one RGBA image per (set, texture id): the page's index image
    coloured by THAT id's CLUT row, alpha 0 where the bake cleared an entry or
    the source had real PNG alpha, and opaque everywhere else. Black is never a
    transparency key.

A legacy bank (no declaration) falls back to its baked pages/PAGE_N.bin, then to
a 16-colour quantise of the material's own image - never to the raw 8-bit art.
"""

import os

import carbank_page
import carbank_palette
import fourbpp
import pages1
import preview_mesh


class PageImage(object):
    """RGBA bytes ready for one GL upload."""

    def __init__(self, width, height, rgba, source):
        self.width = width
        self.height = height
        self.rgba = rgba
        self.source = source


class ProcessedSet(object):
    """One page after the bake: indices, per-id CLUT rows, transparency."""

    def __init__(self, setid, rows, indices, cleared, mask, pieces, source,
                 paint=None):
        self.set = setid
        self.rows = rows
        self.indices = indices
        self.cleared = cleared
        self.mask = mask
        self.pieces = pieces
        self.source = source
        self.paint = paint

    def row_for(self, tex_id):
        if 0 <= tex_id < len(self.rows):
            return self.rows[tex_id]

        return self.rows[0]

    def image(self, tex_id, rect=None):
        """The page painted with `tex_id`'s row, cropped to `rect` when given.

        `rect` is (x0, y0, x1, y1) in page texels - the wheel sprite's island -
        so a wheel quad samples its own sprite instead of the whole page.
        """
        palette = fourbpp.clut_rgb(self.row_for(tex_id))
        clear = set(self.cleared.get(tex_id, self.cleared.get(0, ())))
        page_w, page_h = fourbpp.PAGE_W, fourbpp.PAGE_H

        if rect:
            x0, y0, x1, y1 = [int(round(v)) for v in rect]
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(page_w, x1), min(page_h, y1)
        else:
            x0, y0, x1, y1 = 0, 0, page_w, page_h

        width, height = x1 - x0, y1 - y0
        pix = bytearray(width * height * 4)

        for y in range(height):
            source_row = (y0 + y) * page_w

            for x in range(width):
                i = source_row + x0 + x
                value = self.indices[i]
                rgb = palette[value] if value < len(palette) else (0, 0, 0)
                transparent = value in clear or (self.mask is not None
                                                 and self.mask[i])
                o = (y * width + x) * 4
                pix[o:o + 4] = bytes((rgb[0], rgb[1], rgb[2],
                                      0 if transparent else 255))

        source = self.source

        if rect:
            source += " [%d,%d,%d,%d]" % tuple(rect)

        return PageImage(width, height, bytes(pix), source)

    def wheel_tex_id(self, island):
        """The texture id whose baked rect owns `island` (0 when unknown)."""
        if not island or not self.pieces:
            return 0

        want = list(island)

        for piece in self.pieces:
            if list(piece["rect"]) == want:
                return piece["id"]

        cx = (island[0] + island[2]) / 2.0
        cy = (island[1] + island[3]) / 2.0

        for piece in self.pieces:
            x0, y0, x1, y1 = piece["rect"]
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return piece["id"]

        return 0

    def keyed_texels(self):
        """How many texels this page draws as see-through."""
        if self.mask is not None:
            return sum(1 for on in self.mask if on)

        cleared = set()

        for indices in self.cleared.values():
            cleared |= set(indices)

        return sum(1 for value in self.indices if value in cleared)


class PreviewPages(object):
    """What one car's materials resolve to, ready for preview_gl."""

    def __init__(self):
        self.images = {}        # material name -> PageImage
        self.sets = {}          # setid -> ProcessedSet
        self.report = []        # human lines for the preview title/log
        self.warnings = []

    def image_for(self, material):
        return self.images.get(material)

    def wheel_image(self, setid, island):
        page = self.sets.get(setid)

        if page is None:
            return None

        return page.image(page.wheel_tex_id(island), rect=island)

    def describe(self):
        return " | ".join(self.report) if self.report else "authored pages"


def quantise_texture(path):
    """A 16-colour RGBA texture at the image's own size (legacy fallback).

    This is still post-processing: Paletteize is the bake's own quantiser, so
    the preview shows the colour crush instead of the authored 8-bit range.
    """
    from PIL import Image

    width, height, pixels, alpha = carbank_page.load_pixels(path)
    key, mask = carbank_page._parse_key("alpha" if alpha is not None else None,
                                        pixels, alpha)
    colours, indices = fourbpp.paletteize(pixels, width, height)
    palette = [tuple(c) for c in colours]
    pix = bytearray(width * height * 4)

    for i, value in enumerate(indices):
        r, g, b = palette[value] if value < len(palette) else (0, 0, 0)
        transparent = mask is not None and mask[i]
        pix[i * 4:i * 4 + 4] = bytes((r, g, b, 0 if transparent else 255))

    return PageImage(width, height, bytes(pix), "quantised %s" % os.path.basename(path))


def _legacy_page(bank_root, setid):
    """A baked page for a bank with no declaration, or None."""
    for folder in ("build/pages", "pages"):
        path = os.path.join(bank_root, folder, "PAGE_%d.bin" % setid)

        if not os.path.isfile(path):
            continue

        with open(path, "rb") as f:
            blob = f.read()

        try:
            rows, raw = pages1.read_page(blob, 0, len(blob))
        except ValueError:
            return None

        if not rows:
            return None

        indices = fourbpp.decode_indices(raw)
        # 0x8000 is Driver 1's STP-marked transparent entry; a page with no
        # declaration has nothing else to go on.
        cleared = {tex_id: {i for i, on in enumerate(pages1.clut_mask(row)) if on}
                   for tex_id, row in enumerate(rows)}

        return ProcessedSet(setid, rows, indices, cleared, None, None,
                            os.path.basename(path) + " (baked)")

    return None


def _declared_page(bank, setid, page, variant, warnings):
    import carbank_bake

    result = carbank_bake.bake_declared_page(bank, setid, page=page,
                                             warnings=warnings)
    spec = {"paints": page.paints, "body": page.paint_body,
            "protect": page.protect}
    rows, paint, _touched = carbank_palette.paint_rows_for(
        spec, result.rows, result.indices, result.pieces, variant)

    return ProcessedSet(setid, rows, result.indices, result.cleared,
                        result.mask, result.pieces,
                        os.path.basename(page.image), paint), result


def _declared_bank(bank, bank_root):
    """A bankjson.Bank for the declaration the preview loaded as raw JSON.

    preview_bank reads bank.json as a dict (it only needs paths); the page bake
    wants the validated object. One conversion here keeps both callers on the
    same declaration.
    """
    if hasattr(bank, "cars"):
        return bank

    import bankjson

    return bankjson.Bank(os.path.join(bank_root, "bank.json"), bank)


def _wheel_page(model, setid, sprites, bank_root, warnings):
    """A synthesized wheel page, through the bake's own helper."""
    import carbank_bake

    raw = getattr(model, "raw", {}) or {}
    build_dir = (os.path.join(bank_root, raw["build_dir"])
                 if raw.get("build_dir") else bank_root)
    result = carbank_bake.bake_wheel_page(model, setid, sprites,
                                          bank_dir=build_dir,
                                          warnings=warnings)

    return ProcessedSet(setid, result.rows, result.indices, result.cleared,
                        result.mask, result.pieces, "synthesised wheel page")


def build_pages(bank_root, bank, obj_path, groups, mats, car=None, variant=0,
                wheel_root=None):
    """PreviewPages for every page set the OBJ's materials (or wheels) name.

    `groups`/`mats` are what preview_mesh.load_obj returned, so the mesh is
    parsed once. `car` adds the declared wheel sets, which no body material
    names but the wheel sprites draw from; `wheel_root` is where that car's
    wheel `png` paths are relative to. `variant` is the civ_clut paint column
    (0 = base colours).
    """
    import carbank_bake

    model = _declared_bank(bank, bank_root)
    pages = PreviewPages()
    declared, conflicts = carbank_bake.declared_pages(model)

    for setid, first, other in conflicts:
        pages.warnings.append("%s and %s declare page set %d differently; %s wins"
                              % (first, other, setid, first))

    wanted = {}                      # setid -> sorted texture ids
    materials = {}                   # material -> (setid, tex id)

    for name in groups:
        page = preview_mesh.material_page(name, obj_path)

        if page is None:
            continue

        setid, tex_id = page
        materials[name] = (setid, tex_id)
        wanted.setdefault(setid, set()).add(tex_id)

    # Wheel sprites are declared on a set no body material names. Bake that set
    # too: the same synthesized page the bank bake writes, so the preview shows
    # the wheel art the game will use. One set, one or three sprite variants.
    sprites_by_set = {}

    for wheel_variant, wheel in ((car or {}).get("wheels") or {}).items():
        if not isinstance(wheel, dict) or wheel.get("set") is None:
            continue

        setid = int(wheel["set"])
        wanted.setdefault(setid, set())
        path = wheel.get("png")
        island = wheel.get("island")

        if path and wheel_root:
            path = os.path.join(wheel_root, path)

        if not path or not island:
            pages.warnings.append(
                "wheels.%s: no png/island, so its wheel page cannot be "
                "previewed" % wheel_variant)
            continue

        if not os.path.exists(path):
            pages.warnings.append("wheels.%s: %s does not exist"
                                  % (wheel_variant, path))
            continue

        sprites_by_set.setdefault(setid, []).append(
            (wheel_variant, path, island))

    textures = {}

    for setid in sorted(wanted):
        declaration = declared.get(setid)

        if declaration is not None:
            processed, _result = _declared_page(model, setid, declaration,
                                                variant, pages.warnings)
        elif setid in sprites_by_set:
            processed = _wheel_page(model, setid, sprites_by_set[setid],
                                    bank_root, pages.warnings)
        else:
            processed = _legacy_page(bank_root, setid)

        if processed is None:
            continue

        pages.sets[setid] = processed
        pages.report.append(
            "page %d: %s%s, %d colour row(s), %d keyed texel(s)" % (
                setid, processed.source,
                " (paint %s)" % processed.paint if processed.paint else "",
                len(processed.rows), processed.keyed_texels()))

    for name, (setid, tex_id) in materials.items():
        processed = pages.sets.get(setid)

        if processed is not None:
            pages.images[name] = processed.image(tex_id)

    # Legacy material names that do not encode a page (or a set with no page on
    # disk) still get a post-processed texture at their own size.
    for name, material in mats.items():
        if name in pages.images:
            continue

        path = material.get("tex")

        if not path:
            continue

        if path not in textures:
            try:
                textures[path] = quantise_texture(path)
            except (ValueError, OSError) as exc:
                pages.warnings.append("texture %s: not previewed (%s)"
                                      % (os.path.basename(path), exc))
                continue

        pages.images[name] = textures[path]

    return pages
