#!/usr/bin/env python3
"""portrait.py: a mode-P image already on the FE CLUT is not rematched.

    python3 -m unittest test_portrait test_bankjson
"""

import io
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import fourbpp
import portrait


def portrait_indices(page):
    return fourbpp.decode_indices(page)


class ClutTests(unittest.TestCase):
    def test_the_embedded_clut_is_the_carback_portrait_row(self):
        colors, raw = portrait.portrait_clut()

        self.assertEqual(len(colors), 16)
        self.assertEqual(len(raw), 32)
        self.assertEqual(raw, portrait.CARBACK_PORTRAIT_CLUT)
        # entry 0 is the black the background file stores, STP set
        word0 = struct.unpack_from("<H", raw, 0)[0]
        self.assertEqual(word0, 0x8000)
        self.assertEqual(colors[0], (0, 0, 0))
        # the repeated olive: rematch would collapse these, pack must not
        self.assertEqual(colors[2], colors[3])
        self.assertNotEqual(colors[2], colors[0])


class PackTests(unittest.TestCase):
    def _matched(self, indices, size):
        from PIL import Image

        clut, _raw = portrait.portrait_clut()
        pal = []

        for rgb in clut:
            pal.extend(min(255, c + 1) for c in rgb)

        pal.extend([0] * (768 - len(pal)))
        pal[83 * 3:83 * 3 + 3] = (1, 2, 3)
        im = Image.new("P", size)
        im.putpalette(pal)
        im.putdata(list(indices))
        return im

    def test_a_matched_index_survives_a_duplicate_clut_colour(self):
        # Entries 2 and 3 display as the same colour. Nearest-colour rematch
        # keeps 2. Pack keeps 3, which is the uncrushed page.
        width, height = portrait.PAGE_W, portrait.SHOWN_H
        src = [3, 7, 83] + [0] * (width * height - 3)
        im = self._matched(src, (width, height))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)

        from PIL import Image

        roundtrip = Image.open(buf)
        roundtrip.load()
        page = portrait.encode_image(roundtrip, "cop.png")
        got = portrait_indices(page)
        self.assertEqual(list(got[:3]), [3, 7, 0])
        self.assertNotEqual(got[2], 83 & 0x0F)
        self.assertTrue(all(v == 0 for v in got[width * height:]))
        expected = bytes([3, 7, 0] + [0] * (width * portrait.PAGE_H - 3))
        self.assertEqual(page, fourbpp.encode_indices(expected))

    def test_a_full_page_keeps_its_bottom_rows(self):
        width, height = portrait.PAGE_W, portrait.PAGE_H
        src = [0] * (width * height)
        src[-1] = 4
        page = portrait.encode_image(self._matched(src, (width, height)))
        got = portrait_indices(page)
        self.assertEqual(got[-1], 4)
        self.assertEqual(len(page), fourbpp.PAGE_INDEX_BYTES)

    def test_a_foreign_palette_is_remapped(self):
        from PIL import Image

        im = Image.new("P", (portrait.PAGE_W, portrait.SHOWN_H))
        im.putpalette([255, 0, 0] * 16 + [0, 0, 0] * 240)
        page = portrait.encode_image(im, "red.png")
        got = portrait_indices(page)
        shown = list(got[:portrait.PAGE_W * portrait.SHOWN_H])
        # index 0 would be a pack. The corner stipples onto the second
        # nearest CARBACK colour; the next texel stays on the nearest.
        self.assertEqual(shown[0], 13)
        self.assertEqual(shown[1], 8)
        self.assertTrue(all(i == 0 for i in got[portrait.PAGE_W * portrait.SHOWN_H:]))


class RemapTests(unittest.TestCase):
    def test_a_clut_colour_is_not_stippled(self):
        from PIL import Image

        clut, _raw = portrait.portrait_clut()
        color = clut[10]
        im = Image.new("RGB", (portrait.PAGE_W, portrait.SHOWN_H), color)
        page = portrait.encode_image(im)
        got = portrait_indices(page)
        shown = got[:portrait.PAGE_W * portrait.SHOWN_H]
        self.assertTrue(all(i == 10 for i in shown))

    def test_a_midpoint_dithers(self):
        from PIL import Image

        clut, _raw = portrait.portrait_clut()
        a = clut[5]   # (8, 8, 8)
        b = clut[1]   # (248, 248, 248)
        mid = tuple((a[c] + b[c]) // 2 for c in range(3))
        im = Image.new("RGB", (portrait.PAGE_W, portrait.SHOWN_H), mid)
        page = portrait.encode_image(im)
        shown = set(portrait_indices(page)[:portrait.PAGE_W * portrait.SHOWN_H])
        # The grey sits nearer other CARBACK entries than the white/black
        # pair it was averaged from. The stipple is still only those two
        # nearest colours, never a flat posterize.
        ranked = sorted(
            range(len(clut)),
            key=lambda i: sum((mid[c] - clut[i][c]) ** 2 for c in range(3)))
        self.assertGreater(len(shown), 1)
        self.assertTrue(shown <= set(ranked[:2]))

    def test_rgba_composites_over_black(self):
        from PIL import Image

        im = Image.new("RGBA", (portrait.PAGE_W, portrait.SHOWN_H), (0, 0, 0, 0))
        page = portrait.encode_image(im)
        shown = portrait_indices(page)[:portrait.PAGE_W * portrait.SHOWN_H]
        # fully transparent lands on black, and the first black entry is 0
        self.assertTrue(all(i == 0 for i in shown))

    def test_the_wrong_size_is_refused(self):
        from PIL import Image

        im = Image.new("RGB", (32, 32), (0, 0, 0))

        with self.assertRaises(portrait.PortraitError) as caught:
            portrait.encode_image(im, "tiny.png")

        self.assertIn("256", str(caught.exception))
        self.assertIn("219", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
