#!/usr/bin/env python3
"""Tests for carbank_import.py -- reading an edited bank back.

The point of these is the gap they close: an edited OBJ used to come back onto a
single solid grey page, so a round trip silently moved every poly to set 1 id 0.

Run from this directory:
    python -m unittest test_carbank_import -v
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_import
import exportd2

OBJ = """mtllib car.mtl
v 0 0 0
v 10 0 0
v 10 10 0
v 0 10 0
vt 0.0 0.0
vt 1.0 0.0
vt 1.0 1.0
usemtl set65_id12
f 1/1 2/2 3/3
usemtl flat
f 1/1 3/2 4/3
usemtl set1_id0
f 1/1 2/2 4/3
"""


class ObjTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="carbank-import-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.obj = os.path.join(self.dir, "clean.obj")

        with open(self.obj, "w") as f:
            f.write(OBJ)

    def test_materials_survive_the_round_trip(self):
        rec = carbank_import.import_car(self.obj)
        pages = [(p["set"], p["id"]) for p in rec["polys"]]

        self.assertEqual(pages, [(65, 12), (1, 0), (1, 0)])

    def test_uvs_are_scaled_to_the_engine_range(self):
        rec = carbank_import.import_car(self.obj)
        uv = rec["polys"][0]["uv"]

        self.assertEqual(uv[0], [0, 255])          # (0,0) with v flipped
        for u, v in uv:
            self.assertTrue(0 <= u <= 255 and 0 <= v <= 255)

    def test_scale_is_recorded(self):
        rec = carbank_import.import_car(self.obj, scale=1.0)

        self.assertEqual(rec["scale"], 1.0)

    def test_a_set_and_id_remap_applies(self):
        rec = carbank_import.import_car(self.obj, set_map={65: 66}, id_map={12: 13})
        pages = [(p["set"], p["id"]) for p in rec["polys"]]

        self.assertEqual(pages[0], (66, 13))
        self.assertEqual(pages[1], (1, 0), "unlisted pages are left alone")

    def test_an_unknown_material_is_refused(self):
        path = os.path.join(self.dir, "bad.obj")

        with open(path, "w") as f:
            f.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nusemtl wing\n"
                    "f 1/1 2/1 3/1\n")

        with self.assertRaises(ValueError) as caught:
            carbank_import.import_car(path)

        self.assertIn("wing", str(caught.exception))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="carbank-page-")
        self.addCleanup(shutil.rmtree, self.dir)

    def _png(self, w=256, h=256, colour=(255, 0, 0)):
        path = os.path.join(self.dir, "page.png")
        pix = bytearray()
        for _ in range(w * h):
            pix.extend(colour)
        exportd2.write_png_rgb(path, w, h, pix)
        return path

    def test_a_page_reads_back_as_rgb(self):
        path = self._png(colour=(0, 255, 0))
        w, h, pixels = carbank_import.read_page(path)

        self.assertEqual((w, h), (256, 256))
        self.assertEqual(pixels[0], (0, 255, 0))

    def test_a_wrongly_sized_page_is_refused(self):
        path = self._png(w=64, h=64)

        with self.assertRaises(ValueError) as caught:
            carbank_import.read_page(path)

        self.assertIn("64x64", str(caught.exception))

    def test_a_rect_reads_just_that_rectangle(self):
        path = self._png(colour=(10, 20, 30))
        pixels = carbank_import.read_rect(path, (4, 8, 2, 2))

        self.assertEqual(len(pixels), 4)
        self.assertEqual(pixels[0], (10, 20, 30))

    def test_a_rect_outside_the_page_is_refused(self):
        path = self._png()

        with self.assertRaises(ValueError) as caught:
            carbank_import.read_rect(path, (255, 255, 2, 2))

        self.assertIn("outside", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
