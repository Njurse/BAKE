#!/usr/bin/env python3
"""Overlapping UV islands: a material's texels keep its own colours.

    python -m unittest test_page_overlap -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_page
import fourbpp

W = H = fourbpp.PAGE_W


class OverlapTest(unittest.TestCase):
    def test_triangles_sharing_a_bbox_do_not_repaint_each_other(self):
        # Two triangles split one 64x64 square on its diagonal: their bboxes
        # overlap, but each triangle's own texels use 10 colours.
        # Planning by bbox counted 20 and spilled one row's indices onto the
        # other triangle's texels.
        pixels = [(8, 8, 8)] * (W * H)
        upper = [(16 + 8 * i, 0, 0) for i in range(10)]
        lower = [(0, 16 + 8 * i, 0) for i in range(10)]

        for y in range(64):
            for x in range(64):
                band = (x + y) % 10
                pixels[y * W + x] = upper[band] if x > y else lower[band] if x < y else (8, 8, 8)

        islands = {1: [[(3, 0), (63, 0), (63, 60)]], 2: [[(0, 3), (60, 63), (0, 63)]]}
        indices, rows, pieces = carbank_page.plan_islands(pixels, W, H, islands, True)

        for tex_id, uvs in ((1, islands[1][0]), (2, islands[2][0])):
            cover = bytearray(W * H)
            carbank_page._cover_poly(cover, W, H, uvs)

            for i, bit in enumerate(cover):
                if bit:
                    self.assertEqual(tuple(rows[tex_id][indices[i]]),
                                     fourbpp.snap555(pixels[i]), (tex_id, i % W, i // W))

        self.assertEqual({p["id"] for p in pieces if p.get("src") is not None}, {1, 2})

    def test_dent_art_reads_through_the_material_row(self):
        # denting.c shifts a dented poly's u by 64*n. The damage art at u+128
        # is covered by no poly; it must sit on the material's own row rather
        # than on a leftover row of its own.
        pixels = [(8, 8, 8)] * (W * H)
        clean = [(16 + 8 * i, 0, 0) for i in range(6)]
        dented = [(0, 0, 16 + 8 * i) for i in range(6)]

        for y in range(64):
            for x in range(64):
                pixels[y * W + x] = clean[(x + y) % 6]
                pixels[y * W + x + 128] = dented[(x * y) % 6]
            for x in range(64, 128):
                pixels[y * W + x] = (200, 200, 200 - (x % 12) * 8)

        islands = {1: [[(0, 0), (63, 0), (63, 63), (0, 63)]],
                   2: [[(64, 0), (127, 0), (127, 63), (64, 63)]]}
        shadows, shift = carbank_page.dent_shadows(pixels, W, H, islands)
        self.assertEqual(shift, 128)
        indices, rows, _pieces = carbank_page.plan_islands(pixels, W, H, islands, True)

        for y in range(64):
            for x in range(128, 192):
                i = y * W + x
                self.assertEqual(tuple(rows[1][indices[i]]), fourbpp.snap555(pixels[i]), (x, y))

    def test_body_splits_leave_ids_for_the_wheels(self):
        # 40 one-poly islands of 17 colours each would take every free id; the
        # wheel sprite composited on the page still needs its own row.
        pixels = [(8, 8, 8)] * (W * H)
        uvs = []

        for n in range(40):
            x0, y0 = (n % 8) * 24, (n // 8) * 24
            for y in range(20):
                for x in range(20):
                    pixels[(y0 + y) * W + x0 + x] = (8 + 8 * ((x + y + n) % 17), 8 * (n % 30), 64)
            uvs.append([(x0, y0), (x0 + 19, y0), (x0 + 19, y0 + 19), (x0, y0 + 19)])

        wheel = {"uv": [(192, 192), (255, 192), (255, 255), (192, 255)], "kind": "wheel", "variant": "clean"}
        _indices, rows, pieces = carbank_page.plan_islands(pixels, W, H, {1: uvs}, True, extra_islands=[wheel])
        ids = [p["id"] for p in pieces if p.get("kind") == "wheel"]
        self.assertEqual(len(ids), 1)
        self.assertLess(ids[0], carbank_page.MAX_TEX_IDS)


if __name__ == "__main__":
    unittest.main()
