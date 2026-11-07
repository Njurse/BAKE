#!/usr/bin/env python3
"""Tests for the synthesized wheel page - a wheel set with no `textures` entry.

BIGRIG declares its wheel sprites on page set 20 but never declares a page
image for 20, so the bake carried no set 20 at all and the game drew the host
city's wheel texture. The bake must create that page from the sprites and put
it in the manifest/textureinfo.

Run from this directory:
    python -m unittest test_wheel_page -v
"""

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
import carbank_bake
import exportd2
import fourbpp
import pages1
import preview_mesh
import preview_pages

OBJ = """v 0 0 0
v 1 0 0
v 1 1 0
vt 0 0
vt 1 0
vt 1 1
usemtl PAGE_7
f 1/1 2/2 3/3
"""


class WheelPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.build = os.path.join(self.root, "build")
        os.makedirs(os.path.join(self.root, "cars", "CAR"))

    def _write(self, rel, obj):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

        return path

    def _bank(self):
        from PIL import Image

        page = Image.new("RGB", (256, 256), (192, 224, 240))
        page.save(os.path.join(self.root, "cars", "CAR", "page7.png"))

        wheel = Image.new("RGBA", (64, 64), (0, 0, 0, 0))

        for y in range(16, 48):
            for x in range(16, 48):
                wheel.putpixel((x, y), (255, 0, 0, 255))

        wheel.save(os.path.join(self.root, "cars", "CAR", "wheel.png"))
        with open(os.path.join(self.root, "cars", "CAR", "car.obj"), "w",
                  encoding="utf-8") as f:
            f.write(OBJ)
        self._write("cars/CAR/handling.json", {
            "model": 1,
            "clean": "car.obj",
            "textures": {"7": {"png": "page7.png"}},
            "wheels": {"clean": {"png": "wheel.png", "set": 20,
                                 "island": [192, 64, 256, 128]}},
        })
        path = self._write("bank.json", {
            "city": "TEST",
            "build_dir": "build",
            "declare": {
                "schema": 1,
                "name": "TEST",
                "scale": 1,
                "order": [{"slot": 1, "car": "CAR"}],
                "palette_from_pages": True,
                "cars": {"CAR": "cars/CAR/handling.json"},
            },
        })
        return bankjson.load(path)

    def test_a_wheel_set_without_a_texture_declaration_is_baked(self):
        bank = self._bank()
        blocks = os.path.join(self.build, "models")
        manifest, warnings = carbank_bake.build_manifest(
            bank, self.build, 1, blocks)

        sets = {page["set"] for page in manifest["pages"]}
        self.assertIn(20, sets, "the wheel page must be in the manifest")

        path = os.path.join(self.build, "pages", "PAGE_20.bin")
        self.assertTrue(os.path.isfile(path), "no page 20 was written")

        with open(path, "rb") as handle:
            blob = handle.read()

        rows, raw = pages1.read_page(blob, 0, len(blob))
        indices = fourbpp.decode_indices(raw)

        # the sprite's centre is red; its corner is the cleared backdrop
        centre = indices[96 * 256 + 224]
        corner = indices[64 * 256 + 192]
        palette = fourbpp.clut_rgb(rows[0])

        self.assertGreater(palette[centre][0], 200)
        self.assertLessEqual(max(palette[centre][1:]), 16)
        self.assertEqual(palette[corner], (0, 0, 0))
        self.assertEqual(len([c for c in palette if c == (0, 0, 0)]), 1,
                         "only the cleared wheel backdrop may be 0x0000")

        # textureinfo must name set 20 or the assembler carves no page for it
        with open(os.path.join(self.build, "textureinfo.bin"), "rb") as f:
            _head, perms, specs = exportd2.split_textureinfo(f.read())

        available = {s for s, _z in perms} | {s for s, _z in specs}
        self.assertIn(20, available)
        self.assertTrue(warnings)

    def test_the_preview_builds_the_same_wheel_page(self):
        bank = self._bank()

        with open(os.path.join(self.root, "bank.json"), encoding="utf-8") as f:
            raw = json.load(f)
        with open(os.path.join(self.root, "cars", "CAR", "handling.json"),
                  encoding="utf-8") as f:
            car = json.load(f)

        obj_path = os.path.join(self.root, "cars", "CAR", "car.obj")
        _verts, _uvs, groups, mats = preview_mesh.load_obj(obj_path)
        pages = preview_pages.build_pages(
            self.root, raw, obj_path, groups, mats, car, 0,
            wheel_root=os.path.join(self.root, "cars", "CAR"))

        self.assertIn(20, pages.sets)
        self.assertIn("synthesised", pages.sets[20].source)

        image = pages.wheel_image(20, [192, 64, 256, 128])
        self.assertIsNotNone(image)
        self.assertEqual((image.width, image.height), (64, 64))

        def pixel(x, y):
            i = (y * image.width + x) * 4
            return tuple(image.rgba[i:i + 4])

        self.assertGreater(pixel(24, 24)[0], 200, "the red sprite is missing")
        self.assertEqual(pixel(0, 0)[3], 0,
                         "the sprite's transparent corner must stay clear")


if __name__ == "__main__":
    unittest.main(verbosity=2)
