#!/usr/bin/env python3
"""Tests for preview_pages.py - the preview shows the BAKED page, not the art.

The preview used to upload the authored 8-bit page and turn every black texel
into alpha 0, so it showed colours the game cannot draw and hid black paint.
These pin the three claims that replace it: the textures come from the page
bake (<= 16 colours per row), only the bake's cleared/alpha texels are see-
through, and a paint column is the same recolour LUMP_PALLET writes.

No pygame or OpenGL is needed - this is the image-building half of preview_car.

Run from this directory:
    python -m unittest test_preview_pages -v
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import preview_mesh
import preview_pages

MAGENTA = (255, 0, 255)
BLACK = (0, 0, 0)
BODY = (192, 224, 240)

OBJ = """v 0 0 0
v 1 0 0
v 1 1 0
vt 0 0
vt 1 0
vt 1 1
usemtl PAGE_7
f 1/1 2/2 3/3
"""


def _page_rgb(width=256, height=256):
    """A page with a magenta backdrop, black art and a body colour."""
    pixels = [BODY] * (width * height)

    for y in range(16):
        for x in range(16):
            pixels[y * width + x] = MAGENTA

    for y in range(4, 12):
        for x in range(4, 12):
            pixels[y * width + x] = BLACK

    return pixels


class PreviewPagesTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "cars", "CAR"))

    def _write(self, rel, text, mode="w"):
        path = os.path.join(self.root, rel)

        if "b" not in mode:
            os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, mode) as f:
            f.write(text)

        return path

    def _bank(self, texture, obj=OBJ):
        """Build a one-car bank and return (bank, car, obj, groups, mats)."""
        import json

        from PIL import Image

        page = texture.pop("pixels")
        mode = texture.pop("mode", "RGB")
        alpha = texture.pop("alpha", None)

        if mode == "RGBA":
            page = [tuple(p) + (alpha[i] if alpha else 255,)
                    for i, p in enumerate(page)]

        image = Image.new(mode, (256, 256))
        image.putdata(page)
        image.save(os.path.join(self.root, "cars", "CAR", "page7.png"))

        self._write("cars/CAR/car_clean.obj", obj)
        self._write("cars/CAR/handling.json", """{
  "model": 1,
  "clean": "car_clean.obj",
  "textures": {"7": %s}
}
""" % json.dumps(texture))
        path = self._write("bank.json", """{
  "city": "TEST",
  "declare": {
    "schema": 1,
    "name": "TEST",
    "order": [{"slot": 1, "car": "CAR"}],
    "palette_from_pages": true,
    "cars": {"CAR": "cars/CAR/handling.json"}
  }
}
""")

        with open(path, encoding="utf-8") as f:
            bank = json.load(f)
        with open(os.path.join(self.root, "cars", "CAR", "handling.json"),
                  encoding="utf-8") as f:
            car = json.load(f)

        obj_path = os.path.join(self.root, "cars", "CAR", "car_clean.obj")
        groups, mats = preview_mesh.load_obj(obj_path)[2:]
        return bank, car, obj_path, groups, mats

    def _build(self, texture, variant=0, obj=OBJ):
        bank, car, obj_path, groups, mats = self._bank(texture, obj)
        return preview_pages.build_pages(self.root, bank, obj_path, groups,
                                         mats, car, variant)

    def test_black_art_is_opaque_and_the_colour_key_is_clear(self):
        pages = self._build({"png": "page7.png", "transparent": "ff00ff",
                             "transparent_tolerance": 0, "pixels": _page_rgb()})
        image = pages.images["PAGE_7"]
        stride = image.width * 4

        def pixel(x, y):
            i = y * stride + x * 4
            return tuple(image.rgba[i:i + 4])

        self.assertEqual(pixel(6, 6), (0, 0, 0, 255),
                         "black art must stay visible in the preview")
        self.assertEqual(pixel(1, 1)[3], 0, "the keyed backdrop is see-through")

    def test_the_preview_uses_the_baked_sixteen_colours(self):
        pixels = _page_rgb()

        for y in range(64, 68):
            for x in range(128):
                pixels[y * 256 + x] = ((x * 4) % 256, (x * 7) % 256,
                                       (x * 11) % 256)

        pages = self._build({"png": "page7.png", "pixels": pixels})
        image = pages.images["PAGE_7"]
        colours = {image.rgba[i:i + 3]
                   for i in range(0, len(image.rgba), 4)
                   if image.rgba[i + 3]}

        self.assertGreater(len({pixels[y * 256 + x] for y in range(64, 68)
                                for x in range(128)}), 16,
                           "the source must carry more than a page can hold")
        self.assertLessEqual(len(colours), 16,
                             "the preview must show the page's CLUT, not the art")

    def test_real_alpha_is_see_through(self):
        pixels = _page_rgb()
        alpha = [255] * (256 * 256)

        for y in range(32, 48):
            for x in range(32, 48):
                pixels[y * 256 + x] = (10, 20, 30)
                alpha[y * 256 + x] = 0

        pages = self._build({"png": "page7.png", "transparent": "alpha",
                             "pixels": pixels, "mode": "RGBA",
                             "alpha": alpha})
        image = pages.images["PAGE_7"]
        index = (40 * image.width + 40) * 4

        self.assertEqual(image.rgba[index + 3], 0,
                         "PNG alpha must survive into the preview")

    def test_a_paint_column_recolours_only_the_body(self):
        pages = self._build({
            "png": "page7.png", "transparent": "ff00ff",
            "transparent_tolerance": 0,
            "paint_body": ["c0e0f0"], "paints": {"red": "c02020"},
            "pixels": _page_rgb()}, variant=1)
        image = pages.images["PAGE_7"]
        stride = image.width * 4

        def pixel(x, y):
            i = y * stride + x * 4
            return tuple(image.rgba[i:i + 4])

        body = pixel(200, 200)
        self.assertGreater(body[0], body[2],
                           "the body should take the paint's hue")
        self.assertEqual(body[3], 255)
        self.assertEqual(pixel(6, 6), (0, 0, 0, 255),
                         "a paint must not make black art transparent")
        self.assertEqual(pixel(1, 1)[3], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
