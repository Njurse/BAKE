#!/usr/bin/env python3
"""CLUT split, civ-row map, and image paints.

    python -m unittest test_page_split -v
"""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_page
import carbank_palette
import fourbpp
import pages1

W = H = fourbpp.PAGE_W


def _blank():
    return [(8, 8, 8)] * (W * H)


def _paint(pixels, x0, y0, colours, size=8):
    for i, colour in enumerate(colours):
        for y in range(size):
            for x in range(size):
                pixels[(y0 + y) * W + (x0 + i * size + x)] = colour
    return pixels


def _quad(x0, y0, x1, y1):
    return [(x0, y0), (x1 - 1, y0), (x1 - 1, y1 - 1), (x0, y1 - 1)]


def _reds(n):
    return [fourbpp.snap555((16 + i * 16, 0, 0)) for i in range(n)]


def _blues(n):
    return [fourbpp.snap555((0, 0, 16 + i * 16)) for i in range(n)]


class SplitTests(unittest.TestCase):
    def test_sixteen_snapped_colours_stay_one_row(self):
        # A 256-colour PNG whose low bits all snap to 16 RGB555 shades. The
        # background is one of those shades, so the page is not a 17th colour.
        shades = [fourbpp.snap555((16 + i * 8, 40, 80)) for i in range(16)]
        shades = list(dict.fromkeys(shades))
        self.assertEqual(len(shades), 16)
        pixels = [shades[i % 16] for i in range(W * H)]
        islands = {0: [_quad(0, 0, W, H)]}
        self.assertEqual(carbank_page.count_snapped(pixels), 16)
        self.assertFalse(carbank_page.should_split(pixels, W, H, islands, "auto"))

    def test_two_panels_that_do_not_share_a_row_split(self):
        pixels = _blank()
        _paint(pixels, 0, 0, _reds(10))
        _paint(pixels, 128, 0, _blues(10))
        islands = {0: [_quad(0, 0, 80, 8), _quad(128, 0, 208, 8)]}

        self.assertTrue(carbank_page.should_split(pixels, W, H, islands, "auto"))
        self.assertFalse(carbank_page.should_split(pixels, W, H, islands, "off"))

        indices, rows, pieces = carbank_page.plan_islands(pixels, W, H, islands, True)
        ids = {p["id"] for p in pieces}
        self.assertGreater(len(ids), 1)
        self.assertNotIn((0, 0, 0), rows[0])

        left = rows[0][indices[0]]
        self.assertGreater(left[0], left[2])

        right_id = next(p["id"] for p in pieces if p["rect"][0] >= 128)
        right = rows[right_id][indices[128]]
        self.assertGreater(right[2], right[0])

    def test_set65_id13_keeps_its_id(self):
        pixels = _blank()
        _paint(pixels, 0, 0, _reds(10))
        _paint(pixels, 128, 32, _blues(10))
        islands = {0: [_quad(0, 0, 80, 8)],
                   13: [_quad(128, 32, 208, 40)]}

        _indices, rows, pieces = carbank_page.plan_islands(pixels, W, H, islands, True)

        self.assertIn(13, rows)
        self.assertTrue(any(p["id"] == 13 and p["src"] == 13 for p in pieces))
        blob, _ordered = carbank_page.pack_indexed(_indices, rows, pieces=pieces)
        page_rows, raw = pages1.read_page(blob, 0, len(blob))
        self.assertGreaterEqual(len(page_rows), 14)
        self.assertEqual(fourbpp.decode_indices(raw), bytes(_indices))
        # near-black is not minted as the transparent marker
        self.assertNotIn((0, 0, 0), fourbpp.clut_rgb(page_rows[13]))

        import tempfile
        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, "pages"))

        with open(os.path.join(folder, "pages", "PAGE_65.bin"), "wb") as handle:
            handle.write(blob)

        emitted = carbank_palette.rows_from_pages(folder, {65})
        self.assertIn(13, {record["texnum"] for record in emitted})
        self.assertEqual(emitted[0]["texnum"], 0)
        self.assertEqual(emitted[0]["clut"], page_rows[0])

    def test_civ_rows_past_eight_fall_back_to_row_zero(self):
        mapping = carbank_page.civ_row_map(range(1, 12))
        overflow = [m for m in mapping if m["overflow"]]

        self.assertEqual(len(mapping), 11)
        self.assertEqual(len(overflow), 3)
        self.assertTrue(all(m["row"] == 0 for m in overflow))
        text = carbank_page.clut_dump_text(
            [{"tpageindex": m["set"], "texnum": 0, "palette": 0,
              "clut": fourbpp.rgb_to_clut([(8, 8, 8)] * 16)} for m in mapping])
        self.assertIn("OVERFLOW", text)
        self.assertIn("set 1 -> civ row 0", text)


class PaintImageTests(unittest.TestCase):
    def test_an_image_repaints_by_index(self):
        colours = [(255, 0, 0), (0, 255, 0)] + [(8, 8, 8)] * 14
        base = fourbpp.rgb_to_clut(colours)
        # STP on the first entry must survive the repaint
        base = bytearray(base)
        struct.pack_into("<H", base, 0, struct.unpack_from("<H", base, 0)[0] | 0x8000)
        indices = bytes([0, 1, 0, 1])
        paint = [(0, 0, 255), (255, 255, 0), (0, 0, 255), (255, 255, 0)]
        row = carbank_palette.paint_from_image(bytes(base), indices, paint)
        rgb = fourbpp.clut_rgb(row)

        self.assertEqual(rgb[0], fourbpp.snap555((0, 0, 255)))
        self.assertEqual(rgb[1], fourbpp.snap555((255, 255, 0)))
        self.assertTrue(struct.unpack_from("<H", row, 0)[0] & 0x8000)
        self.assertEqual(rgb[2], (8, 8, 8))


if __name__ == "__main__":
    unittest.main()
