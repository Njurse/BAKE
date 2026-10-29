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


if __name__ == "__main__":
    unittest.main(verbosity=2)
