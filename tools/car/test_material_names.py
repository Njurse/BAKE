#!/usr/bin/env python3
"""PAGE_N author material names: python -m unittest test_material_names -v"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import bankjson
import obj2geom


class MaterialToPage(unittest.TestCase):
    def test_page_n_is_whole_page(self):
        for name in ("PAGE_10", "PAGE_10.png", "page_10", "PAGE_10.001",
                     "PAGE_10_ft"):
            self.assertEqual(obj2geom.material_to_page(name, "t"), (10, 0), name)

    def test_stock_set_id(self):
        self.assertEqual(obj2geom.material_to_page("set5_id12", "t"), (5, 12))
        self.assertEqual(obj2geom.material_to_page("set5_id12_ft", "t"), (5, 12))

    def test_flat(self):
        self.assertIsNone(obj2geom.material_to_page("flat", "t"))
        self.assertIsNone(obj2geom.material_to_page("", "t"))

    def test_unknown_names_say_what_to_do(self):
        with self.assertRaises(ValueError) as cm:
            obj2geom.material_to_page("Material.001", "car.obj")
        self.assertIn("PAGE_10", str(cm.exception))

    def test_default_page_still_maps_unknown(self):
        seen = []
        self.assertEqual(obj2geom.material_to_page("Body", "t", (1, 0), seen), (1, 0))
        self.assertEqual(seen, ["Body"])


class AutoWire(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "textures"))
        with open(os.path.join(self.dir, "car.obj"), "w") as fh:
            fh.write("v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl PAGE_10\nf 1 2 3\n"
                     "usemtl PAGE_13\nf 1 2 3\n")
        for n in (10, 13):
            open(os.path.join(self.dir, "textures", "PAGE_%d.png" % n), "wb").close()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def car(self, textures):
        return bankjson.Car("c", "handling.json",
                            {"clean": "car.obj", "textures": textures},
                            self.dir, "w")

    def test_missing_page_is_wired_from_png(self):
        car = self.car({"13": "textures/PAGE_13.png"})
        self.assertEqual([t.set for t in car.textures], [10, 13])
        self.assertTrue(car.textures[0].image.endswith("PAGE_10.png"))

    def test_the_old_page_png_name_still_wires(self):
        os.remove(os.path.join(self.dir, "textures", "PAGE_10.png"))
        open(os.path.join(self.dir, "textures", "page10.png"), "wb").close()
        car = self.car({"13": "textures/PAGE_13.png"})
        self.assertEqual(car.textures[0].set, 10)
        self.assertTrue(car.textures[0].image.endswith("page10.png"))

    def test_no_png_is_a_clear_error(self):
        os.remove(os.path.join(self.dir, "textures", "PAGE_10.png"))
        with self.assertRaises(bankjson.BankError) as cm:
            self.car({"13": "textures/PAGE_13.png"})
        self.assertIn("textures.10", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
