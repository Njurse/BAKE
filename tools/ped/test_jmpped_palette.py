#!/usr/bin/env python3
"""Palette-row allocation for custom characters."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_bake as jb
import jmpped_core as jc
import jmpped_images as ji

RED = [(255, 0, 0)] * 4
BLUE = [(0, 0, 255)] * 4


class PaletteRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _char(self, specs):
        """specs: [(part, pixels, preferred row, x)]"""
        parts = []
        for slot, (name, pixels, preferred, x) in enumerate(specs):
            rel = "characters/test/textures/crops/%s.png" % name
            ji.save_rgb_png(os.path.join(self.root, rel.replace("/", os.sep)),
                            2, 2, pixels)
            parts.append({
                "slot": slot,
                "part": name,
                "texture_page_sets": [4],
                "crops": [{
                    "page_set": 4,
                    "texture": preferred,
                    "palette_row": preferred,
                    "rect": [x, 0, 2, 2],
                    "rgb": rel,
                }],
                "materials": [{"id": preferred, "page_set": 4}],
            })
        return {"name": "Test", "parts": parts}

    def test_equal_palettes_share_a_row(self):
        char = self._char([("A", RED, 5, 0), ("B", RED, 9, 2)])
        rows = jb.allocate_palette_rows(self.root, char, {4: 24})
        self.assertEqual(rows["A"], {"all": 5})
        self.assertEqual(rows["B"], {"all": 5})

    def test_different_palettes_move_to_a_free_row(self):
        char = self._char([("A", RED, 5, 0), ("B", BLUE, 5, 2)])
        rows = jb.allocate_palette_rows(self.root, char, {4: 24})
        self.assertEqual(rows["A"], {"all": 5})
        self.assertEqual(rows["B"], {"all": 6})
        self.assertEqual(char["parts"][0]["crops"][0]["_alloc_row"], 5)
        self.assertEqual(char["parts"][1]["crops"][0]["_alloc_row"], 6)

    def test_a_full_page_is_an_error(self):
        char = self._char([("A", RED, 0, 0), ("B", BLUE, 0, 2)])
        with self.assertRaises(jc.JMPPEDError):
            jb.allocate_palette_rows(self.root, char, {4: 1})


if __name__ == "__main__":
    unittest.main()
