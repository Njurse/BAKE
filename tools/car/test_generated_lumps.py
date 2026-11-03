#!/usr/bin/env python3
"""The lumps a declared bank GENERATES (no donor level): textureinfo,
texturenames and modelnames read back the way the engine and exporter read them."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_bake
import exportd2
import pages1


class GeneratedLumps(unittest.TestCase):
    def test_textureinfo_lists_every_page_with_its_size(self):
        sizes = {1: 33060, 4: 30960, 13: 33060}
        body, names = carbank_bake.generated_textureinfo(sizes)
        ti = pages1.parse_texture_info(body)

        self.assertEqual(ti["tpage_amount"], 14)
        self.assertEqual(sorted(ti["permlist"] + ti["speclist"]),
                         sorted(sizes.items()))
        self.assertEqual(names, b"")
        _head, perms, specs = exportd2.split_textureinfo(body)
        self.assertEqual(sorted(perms + specs), sorted(sizes.items()))

    def test_more_than_sixteen_pages_spill_to_the_special_list(self):
        sizes = {s: 1000 for s in range(1, 21)}
        body, _names = carbank_bake.generated_textureinfo(sizes)
        ti = pages1.parse_texture_info(body)

        self.assertEqual(ti["nperms"], 16)
        self.assertEqual(len(ti["speclist"]), 4)

    def test_wheel_islands_are_named_details(self):
        wheels = [{"variant": "clean", "set": 5, "island": [0, 0, 32, 32]},
                  {"variant": "damaged", "set": 5, "island": [32, 0, 64, 32]},
                  None]
        body, names = carbank_bake.generated_textureinfo({5: 100}, wheels)
        ti = pages1.parse_texture_info(body)
        got = {d["name"]: (d["x"], d["y"], d["width"], d["height"])
               for _p, d in exportd2.texinfo_details(ti, names)}

        self.assertEqual(got, {"WHLCLEAN": (0, 0, 32, 32),
                               "WHLBOTH": (32, 0, 32, 32)})

    def test_page_past_127_is_refused(self):
        with self.assertRaises(SystemExit):
            carbank_bake.generated_textureinfo({128: 10})

    def test_modelnames_name_each_filled_slot(self):
        body = carbank_bake.generated_modelnames([(4, "REDBUICK_model4")])

        self.assertEqual(body.split(b"\0")[:5], [b"", b"", b"", b"", b"REDBUICK_model4"])


if __name__ == "__main__":
    unittest.main()
