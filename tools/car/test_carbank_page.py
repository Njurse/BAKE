#!/usr/bin/env python3
"""Tests for carbank_page.py -- the page bake, and its transparent colour key.

The engine has no per-texel alpha. Its transparent marker is a CLUT entry of
0x0000, so an art backdrop - a pink sky behind a wheel sprite, say - bakes as an
OPAQUE square unless the key clears that entry. These pin the key's behaviour and
the fact that leaving it out changes nothing.

Run from this directory:
    python -m unittest test_carbank_page -v
"""

import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_page
import fourbpp
import pages1

MAGENTA = (255, 0, 255)
GREY = (128, 128, 128)
DARK = (32, 32, 32)

SPRITE = 8          # an 8x8 block of art in the top-left corner


def _page(magenta_everywhere=False):
    """A page whose top-left 8x8 is art on a magenta backdrop."""
    pixels = []

    for y in range(fourbpp.PAGE_H):
        for x in range(fourbpp.PAGE_W):
            if magenta_everywhere:
                pixels.append(MAGENTA)
            elif x < SPRITE and y < SPRITE and 2 <= x <= 5 and 2 <= y <= 5:
                pixels.append(DARK)
            elif x < SPRITE and y < SPRITE:
                pixels.append(MAGENTA)
            else:
                pixels.append(GREY)

    return pixels


def _index_of(pixels, x, y):
    return y * fourbpp.PAGE_W + x


class NoKeyTests(unittest.TestCase):
    def test_without_a_key_nothing_is_made_transparent(self):
        # the pre-existing behaviour: the backdrop keeps its own entry
        blob, colours, indices, error, background = carbank_page.bake_page(_page())

        self.assertIsNone(background)
        self.assertEqual(indices[_index_of(None, 0, 0)], 0)     # magenta first
        self.assertEqual(struct.unpack_from("<H", blob, 4)[0], 0x7C1F)

    def test_a_page_round_trips_through_the_engine_reader(self):
        blob, colours, indices, _error, _bg = carbank_page.bake_page(_page())
        rows, raw = pages1.read_page(blob, 0, len(blob))

        self.assertEqual(len(rows), 1)
        self.assertEqual(fourbpp.decode_indices(raw), bytes(indices))


class KeyTests(unittest.TestCase):
    def test_every_keyed_texel_lands_on_one_index(self):
        blob, colours, indices, error, background = carbank_page.bake_page(
            _page(), key=MAGENTA)

        self.assertIsNotNone(background)
        at = background["index"]

        self.assertEqual(indices[_index_of(None, 0, 0)], at)
        self.assertEqual(indices[_index_of(None, 7, 7)], at)
        self.assertNotEqual(indices[_index_of(None, 8, 0)], at,
                            "a texel outside the sprite must not join the key")
        self.assertEqual(background["stray"], 0)

    def test_the_keyed_entry_is_transparent_in_every_row(self):
        # 0x0000 is the value the engine draws as transparent
        blob, _colours, _indices, _error, background = carbank_page.bake_page(
            _page(), npal_keep=2, key=MAGENTA)

        at = background["index"]

        self.assertEqual(struct.unpack_from("<i", blob, 0)[0], 2)

        for row in range(2):
            offset = 4 + row * fourbpp.CLUT_ROW_BYTES
            self.assertEqual(struct.unpack_from("<H", blob, offset + at * 2)[0],
                             0, "row %d leaves index %d opaque" % (row, at))

    def test_the_art_survives(self):
        _blob, _colours, indices, error, background = carbank_page.bake_page(
            _page(), key=MAGENTA)

        # the dark 4x4 centre keeps a real index, and is not the keyed one
        centre = indices[_index_of(None, 3, 3)]

        self.assertNotEqual(centre, background["index"])
        self.assertEqual(error, (0.0, 0), "an exact-colour page should be exact")

    def test_a_tolerance_catches_a_soft_edge(self):
        pixels = _page()
        x, y = 1, 1                                   # an edge texel
        pixels[_index_of(None, x, y)] = (250, 6, 250)  # near-magenta blend

        exact = carbank_page.bake_page(pixels, key=MAGENTA,
                                       tolerance=0)[4]["keyed"]
        soft = carbank_page.bake_page(pixels, key=MAGENTA,
                                      tolerance=8)[4]["keyed"]

        self.assertEqual(soft, exact + 1)

    def test_a_backdrop_that_is_not_in_the_palette_is_refused(self):
        # more than 16 colours, so the quantiser picks: a small backdrop cluster
        # can be folded into an art entry, and clearing that entry would punch a
        # hole in the art instead of the backdrop
        pixels = [((x * 7) % 256, (y * 11) % 256, (x * y) % 256)
                  for y in range(fourbpp.PAGE_H) for x in range(fourbpp.PAGE_W)]

        for i in range(50):
            pixels[i] = MAGENTA

        with self.assertRaises(SystemExit) as caught:
            carbank_page.bake_page(pixels, key=MAGENTA)

        self.assertIn("not in this page's 16 colours", str(caught.exception))

    def test_a_key_that_matches_nothing_is_reported_not_ignored(self):
        _blob, _colours, _indices, _error, background = carbank_page.bake_page(
            _page(), key=(1, 2, 3), tolerance=4)

        self.assertIsNone(background)

    def test_a_texel_that_was_not_keyed_but_shares_the_index_is_counted(self):
        # a mask that keys ONE backdrop texel: the rest of the backdrop is still
        # on that index, so it goes transparent too - and that must be reported
        pixels = _page()
        mask = [False] * len(pixels)
        mask[_index_of(None, 0, 0)] = True

        _blob, _colours, _indices, _error, background = carbank_page.bake_page(
            pixels, mask=mask)

        backdrop = sum(1 for i, p in enumerate(pixels)
                       if p == MAGENTA and not mask[i])

        self.assertEqual(background["keyed"], 1)
        self.assertEqual(background["stray"], backdrop)
        self.assertTrue(backdrop, "the page should have a backdrop to share")


class KeyMaskTests(unittest.TestCase):
    def test_the_mask_is_per_channel_and_inclusive(self):
        pixels = [MAGENTA, (255, 8, 255), (250, 0, 250), DARK]

        self.assertEqual(carbank_page.key_mask(pixels, MAGENTA, 0),
                         [True, False, False, False])
        self.assertEqual(carbank_page.key_mask(pixels, MAGENTA, 8),
                         [True, True, True, False])


class PaletteControlTests(unittest.TestCase):
    """More than 16 colours on a page means some of them are lost. These are the
    two ways to decide WHICH ones."""

    def _busy_page(self):
        # 40 distinct greys plus one proud red: the quantiser folds the greys and
        # drops the red, which is exactly the "headlight grey became body colour"
        # complaint
        pixels = []

        for y in range(fourbpp.PAGE_H):
            for x in range(fourbpp.PAGE_W):
                if x < 40 and y < 40:
                    pixels.append((200, 20, 20))
                else:
                    pixels.append((100 + (x % 40), 100 + (y % 40), 110))

        return pixels

    def test_a_protected_colour_survives_the_quantiser(self):
        pixels = self._busy_page()
        _blob, colours, _i, _e, _b = carbank_page.bake_page(pixels, protect=[(200, 20, 20)])

        self.assertIn((200, 20, 20), [tuple(c) for c in colours])

    def test_a_fixed_palette_is_used_verbatim(self):
        pixels = self._busy_page()
        palette = [(0, 0, 0)] + [(8 * i, 8 * i, 8 * i) for i in range(15)]

        blob, colours, indices, error, _b = carbank_page.bake_page(
            pixels, palette=palette)

        # Pure black is not kept verbatim: it would be stored as 0x0000 and
        # the engine would drop those texels. Every other colour is.
        self.assertEqual([tuple(c) for c in colours],
                         carbank_page._lift_black(palette))
        row = pages1.read_page(blob, 0, len(blob))[0][0]
        self.assertNotEqual(struct.unpack_from("<H", row, 0)[0], 0)
        self.assertEqual(len(set(indices)), len(set(indices)))
        self.assertGreater(error[1], 0, "this palette cannot match the art")

    def test_the_crush_report_names_the_worst_colours(self):
        pixels = self._busy_page()
        _blob, colours, indices, _e, background = carbank_page.bake_page(pixels)
        rows = carbank_page.crush_report(pixels, indices, colours, background)

        self.assertTrue(rows)
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r["error"], -r["texels"])))
        self.assertIn("source", rows[0])


class BlackTreadTests(unittest.TestCase):
    """Black rubber stays opaque. Only the keyed backdrop is 0x0000."""

    def test_black_texels_are_not_the_transparent_word(self):
        pixels = []

        for y in range(fourbpp.PAGE_H):
            for x in range(fourbpp.PAGE_W):
                if x < 32 and y < 32:
                    pixels.append((0, 0, 0))          # tread
                elif x < 48 and y < 48:
                    pixels.append((40, 40, 48))       # sidewall
                else:
                    pixels.append(MAGENTA)            # cutout

        blob, colours, indices, _e, background = carbank_page.bake_page(
            pixels, key=MAGENTA, tolerance=8)
        rows, _raw = pages1.read_page(blob, 0, len(blob))
        tread = indices[0]
        cutout = indices[fourbpp.PAGE_W * 60]
        self.assertEqual(background["index"], cutout)
        self.assertEqual(struct.unpack_from("<H", rows[0], cutout * 2)[0], 0)
        self.assertNotEqual(tread, cutout)
        self.assertNotEqual(struct.unpack_from("<H", rows[0], tread * 2)[0], 0)
        self.assertEqual(tuple(colours[tread]), carbank_page.OPAQUE_BLACK)

    def test_a_black_sprite_corner_does_not_key_the_tread(self):
        # 4 texels: black corner, black tread, magenta cutout, grey hub
        texels = [(0, 0, 0), (0, 0, 0), MAGENTA, (90, 90, 90)]
        mask = carbank_page.sprite_mask(texels, None)

        self.assertEqual(mask, [False, False, True, False])

    def test_a_declared_black_key_still_keys_black(self):
        texels = [(0, 0, 0), (0, 0, 0), MAGENTA]
        mask = carbank_page.sprite_mask(texels, None, key=(0, 0, 0), tolerance=0)

        self.assertEqual(mask, [True, True, False])

    def test_an_alpha_cutout_is_independent_of_black(self):
        texels = [(0, 0, 0), (0, 0, 0), MAGENTA]
        mask = carbank_page.sprite_mask(texels, [255, 255, 0])

        self.assertEqual(mask, [False, False, True])


def _tim(indices, rows, width=16, height=4):
    """A 4bpp TIM. `indices` is width*height bytes, each 0..15."""
    words = width // 4
    packed = fourbpp.encode_indices(bytes(indices), width, height)
    clut = b"".join(rows)
    clut_block = struct.pack("<IHHHH", 12 + len(clut), 0, 0, 16, len(rows)) + clut
    image_block = struct.pack("<IHHHH", 12 + len(packed), 0, 0, words, height) + packed
    return struct.pack("<II", carbank_page.TIM_MAGIC, 8) + clut_block + image_block


class EmbeddedPaletteTests(unittest.TestCase):
    """A TIM's CLUT is the page palette. A paint tint copies it; it does not
    replace the row the mesh draws as column 0."""

    def test_a_tim_round_trips_its_clut_and_indices(self):
        rubber = struct.pack("<H", fourbpp.rgb555((8, 8, 8)))
        body = struct.pack("<H", fourbpp.rgb555((40, 80, 200)) | 0x8000)
        row = rubber + body + b"\x00" * 28
        indices = bytes([0, 1] * 32)  # 16x4
        got = carbank_page.read_tim(_tim(indices, [row]))

        self.assertIsNotNone(got)
        page_idx, rows = got
        self.assertEqual(page_idx[0], 0)
        self.assertEqual(page_idx[1], 1)
        self.assertEqual(page_idx[256], 0)          # padded rows stay index 0
        self.assertEqual(rows[0][:4], rubber + body)
        # STP on the body entry survived
        self.assertEqual(struct.unpack_from("<H", rows[0], 2)[0] & 0x8000, 0x8000)

    def test_a_paint_tint_does_not_replace_the_tim_row(self):
        import os
        import tempfile

        import carbank_palette

        rubber = struct.pack("<H", fourbpp.rgb555((16, 16, 16)))
        body = struct.pack("<H", fourbpp.rgb555((40, 80, 200)))
        row = rubber + body + b"\x00" * 28
        indices = bytes([1] * 64)
        full, clut_rows = carbank_page.read_tim(_tim(indices, [row]))
        pixels = carbank_page.pixels_from_indices(full, clut_rows[0])
        blob, _colours, _idx, background = carbank_page.bake_embedded(
            full, clut_rows, pixels)
        self.assertIsNone(background)
        stored, _raw = pages1.read_page(blob, 0, len(blob))
        self.assertEqual(stored[0], clut_rows[0])

        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, "pages"))

        with open(os.path.join(folder, "pages", "PAGE_2.bin"), "wb") as handle:
            handle.write(blob)

        paints = {2: {"paints": [("red", {"rgb": (200, 20, 20)})],
                     "body": None, "protect": []}}
        emitted = carbank_palette.rows_from_pages(folder, {2}, paints)
        self.assertTrue(emitted)
        self.assertEqual(emitted[0]["palette"], 0)   # lump column 1, not column 0
        self.assertNotEqual(emitted[0]["clut"], stored[0])
        # the page row itself is still the TIM
        again, _ = pages1.read_page(blob, 0, len(blob))
        self.assertEqual(again[0], clut_rows[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
