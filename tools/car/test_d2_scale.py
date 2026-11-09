#!/usr/bin/env python3
"""Demo HAVANA stays at retail size, and slot 0 is not treated as empty.

    python -m unittest test_d2_scale -v
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bankjson
import carbank_bake
import exportd2
import lev1


def _level(junction):
    """A D2 header whose in-memory container holds one junction lump."""
    hdr = struct.pack("<II8i", exportd2.LUMP_LUMPDESC, 16,
                      40, 16, 0, 0, 0, 0, 0, 0)
    return hdr + b"\x00" * 8 + struct.pack("<ii", junction, 0)


class NameShimTests(unittest.TestCase):
    def test_page_png_canonical_name_and_old_alias(self):
        self.assertEqual(bankjson.page_png_name(7), "PAGE_7.png")
        self.assertEqual(bankjson.page_png_aliases(7), ("PAGE_7.png", "page7.png"))
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        old = os.path.join(folder, "page7.png")
        open(old, "wb").close()
        self.assertEqual(bankjson.find_page_png(folder, 7), old)
        new = os.path.join(folder, "PAGE_7.png")
        open(new, "wb").close()
        self.assertEqual(bankjson.find_page_png(folder, 7), new)

    def test_scale_flags_are_one_multiplier(self):
        self.assertIsNone(bankjson.same_scale(None, None))
        self.assertEqual(bankjson.same_scale(1, None), 1.0)
        self.assertEqual(bankjson.same_scale(None, 0.68267), 0.68267)
        self.assertEqual(bankjson.same_scale(1, 1.0), 1.0)
        with self.assertRaises(SystemExit):
            bankjson.same_scale(1, 0.68267)

    def test_help_lists_scale_and_the_old_name(self):
        import subprocess
        for script in ("carbank_bake.py", "exportd2.py"):
            out = subprocess.check_output(
                [sys.executable, os.path.join(HERE, script), "--help"],
                text=True)
            self.assertIn("--scale", out)
            self.assertIn("--obj-scale", out)
            self.assertIn("declare.scale", out)


class SourceTests(unittest.TestCase):
    def test_junction_lump_42_is_the_demo(self):
        self.assertEqual(exportd2.source_kind(_level(exportd2.LUMP_JUNCTIONS2)),
                         "driver2-demo")

    def test_junction_lump_43_is_retail(self):
        self.assertEqual(
            exportd2.source_kind(_level(exportd2.LUMP_JUNCTIONS2_NEW)),
            "driver2")

    def test_demo_and_retail_share_the_engine_scale(self):
        # Measured HAVANA clean lengths, demo/retail: 750/750, 1386/1386.
        # 2048/3000 is the Driver 1 ratio and is not this number.
        self.assertEqual(exportd2.D2_OBJ_SCALE, 1.0)
        self.assertNotAlmostEqual(exportd2.D2_OBJ_SCALE, 2048.0 / 3000.0)

    def test_a_detail_region_uses_the_texture_id_as_its_clut_row(self):
        regions = exportd2.detail_regions([
            {"x": 0, "y": 0, "width": 60, "height": 60, "id": 0},
            {"x": 64, "y": 8, "width": 16, "height": 16, "id": 13},
        ])
        self.assertEqual(regions, [(0, 0, 60, 60, 0), (64, 8, 16, 16, 13)])

    def test_slot_0_is_kept_when_the_level_has_a_cop(self):
        offsets = [(4, 10, 20)] + [(-1, -1, -1)] * 12
        self.assertEqual(exportd2.populated_slots(offsets), [0])

    def test_an_empty_slot_0_is_the_only_way_the_cop_is_absent(self):
        offsets = [(-1, -1, -1)] * 13
        offsets[1] = (8, 12, 16)
        self.assertEqual(exportd2.populated_slots(offsets), [1])


class ObjScaleTests(unittest.TestCase):
    def _model(self):
        return lev1.Model(0, 0, 0, 0, [(0, -32, 309), (121, -32, -375)],
                          [], None, None, [])

    def test_scale_1_writes_the_level_vertices(self):
        fd, path = tempfile.mkstemp(suffix=".obj")
        os.close(fd)
        self.addCleanup(os.remove, path)
        exportd2.write_obj(path, self._model(), "cop", "cop.mtl",
                           scale=exportd2.D2_OBJ_SCALE)
        with open(path) as f:
            verts = [tuple(int(p) for p in line.split()[1:])
                     for line in f if line.startswith("v ")]
        self.assertEqual(verts, [(0, -32, 309), (121, -32, -375)])

    def test_the_d1_ratio_would_shrink_a_demo_car(self):
        fd, path = tempfile.mkstemp(suffix=".obj")
        os.close(fd)
        self.addCleanup(os.remove, path)
        exportd2.write_obj(path, self._model(), "cop", "cop.mtl",
                           scale=2048.0 / 3000.0)
        with open(path) as f:
            verts = [tuple(int(p) for p in line.split()[1:])
                     for line in f if line.startswith("v ")]
        self.assertLess(abs(verts[1][2]), 375)


class BankScaleTests(unittest.TestCase):
    def _bank(self, scale):
        decl = {"schema": 1, "name": "HAVANA",
                "order": [{"slot": 0, "car": "cop"}],
                "cars": {"cop": "car.json"}}
        if scale is not None:
            decl["scale"] = scale
            decl["donor"] = {"city": "HAVANA", "source": "driver2-demo",
                             "obj_scale": 1}
        raw = {"city": "HAVANA", "declare": decl}
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        car = os.path.join(folder, "car.json")
        with open(car, "w") as f:
            f.write('{"clean": "clean.obj", "atlas": "atlas.png"}\n')
        with open(os.path.join(folder, "clean.obj"), "w") as f:
            f.write("v 0 0 0\n")
        with open(os.path.join(folder, "atlas.png"), "w") as f:
            f.write("x")
        path = os.path.join(folder, "bank.json")
        with open(path, "w") as f:
            json.dump(raw, f)
        return bankjson.load(path)

    def test_a_demo_bank_bakes_at_1_without_a_cli_scale(self):
        bank = self._bank(1)
        self.assertEqual(carbank_bake.resolve_scale(None, bank), 1.0)

    def test_an_explicit_scale_still_wins(self):
        bank = self._bank(1)
        self.assertEqual(carbank_bake.resolve_scale(0.5, bank), 0.5)

    def test_a_bank_with_no_scale_keeps_the_d1_default(self):
        bank = self._bank(None)
        self.assertIsNone(bank.obj_scale)
        self.assertEqual(carbank_bake.resolve_scale(None, bank),
                         carbank_bake.D1_OBJ_SCALE)

    def test_a_nonsense_scale_is_refused(self):
        with self.assertRaises(bankjson.BankError):
            self._bank(2048)


if __name__ == "__main__":
    unittest.main()
