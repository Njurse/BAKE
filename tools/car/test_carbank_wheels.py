#!/usr/bin/env python3
"""Tests for per-car wheel sets in a car bank.

A bank used to carry ONE wheel set, owned by the first car in slot order: every
other car's `wheels` block was a warning and never reached the bank. It now
carries a set per car, so two cars may crop different pages - or different rects
of one page - and each keeps its own.

Run from this directory:
    python -m unittest test_carbank_wheels -v
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bake
import bankjson
import carbank_bake
import carbank_wheels
import lev1


class WheelFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="carbank-wheels-")
        self.addCleanup(shutil.rmtree, self.dir)

        for name in ("clean.obj", "low.obj", "atlas.png",
                     "wheel_a.png", "wheel_b.png"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("x")

    def wheel(self, png, setid, island):
        """One declared variant, its png named relative to the CAR's folder."""
        return {"png": "../../" + png, "set": setid, "island": list(island)}

    def bank(self, cars):
        """A loaded bank: {car name: wheels dict}, one slot per car, in order."""
        decl_cars = {}

        for name, wheels in cars.items():
            car_dir = os.path.join(self.dir, "cars", name)
            os.makedirs(car_dir, exist_ok=True)

            with open(os.path.join(car_dir, "handling.json"), "w") as f:
                json.dump({"clean": "../../clean.obj",
                           "low": "../../low.obj",
                           "atlas": "../../atlas.png",
                           "wheels": wheels}, f)

            decl_cars[name] = "cars/%s/handling.json" % name

        path = os.path.join(self.dir, "bank.json")

        with open(path, "w") as f:
            json.dump({"city": "TEST", "lev": "TEST.LEV", "lcf": "TEST.LCF",
                       "size": 1,
                       "declare": {"schema": 1, "name": "TEST",
                                   "order": [{"slot": i, "car": name}
                                             for i, name in enumerate(cars)],
                                   "cars": decl_cars}}, f)

        return bankjson.load(path)


class PerCarSetterTests(WheelFixture):
    def test_two_cars_keep_their_own_sets(self):
        """Two cars on one page keep different rects - both sprites survive."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64]),
                     "fast": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {"clean": self.wheel("wheel_b.png", 20, [64, 0, 128, 64]),
                     "fast": self.wheel("wheel_b.png", 20, [64, 0, 128, 64])},
        })

        specs = carbank_bake.wheel_specs(bank, [])
        carbank_bake._check_wheel_clashes(specs)        # must not raise

        self.assertEqual([entry["car"] for entry in specs], ["carA", "carB"])
        self.assertFalse(any(entry.get("inherited") for entry in specs))
        self.assertEqual(specs[0]["wheels"][0]["island"], [0, 0, 64, 64])
        self.assertEqual(specs[1]["wheels"][0]["island"], [64, 0, 128, 64])
        self.assertEqual(carbank_bake.default_wheel_specs(specs),
                         specs[0]["wheels"])

        islands = carbank_bake.wheel_islands(bank)
        self.assertEqual([(variant, island)
                          for variant, _path, island in islands[20]],
                         [("clean", (0, 0, 64, 64)),
                          ("clean", (64, 0, 128, 64))])

    def test_two_cars_differ_only_by_page(self):
        """The same rect on two different pages is not a clash."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {"clean": self.wheel("wheel_b.png", 21, [0, 0, 64, 64])},
        })

        specs = carbank_bake.wheel_specs(bank, [])
        carbank_bake._check_wheel_clashes(specs)

        islands = carbank_bake.wheel_islands(bank)
        self.assertEqual(sorted(islands), [20, 21])

    def test_a_car_without_wheels_inherits_the_first_set(self):
        """A car that declares none gets a COPY of the first declared set."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {},
        })

        specs = carbank_bake.wheel_specs(bank, [])

        self.assertFalse(specs[0].get("inherited"))
        self.assertTrue(specs[1]["inherited"])
        self.assertEqual(specs[1]["inherits_from"], "carA")
        self.assertEqual(specs[1]["wheels"], specs[0]["wheels"])
        self.assertIsNot(specs[1]["wheels"], specs[0]["wheels"])

        # an inherited set is the declaring car's sprite, not a second claim
        carbank_bake._check_wheel_clashes(specs)

    def test_two_cars_on_overlapping_rects_are_refused(self):
        """Overlap - not just equality - is ambiguous and is named."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {"clean": self.wheel("wheel_b.png", 20, [32, 32, 96, 96])},
        })

        specs = carbank_bake.wheel_specs(bank, [])

        with self.assertRaises(SystemExit) as caught:
            carbank_bake._check_wheel_clashes(specs)

        message = str(caught.exception)
        self.assertIn("carA", message)
        self.assertIn("carB", message)
        self.assertIn("page set 20", message)

    def test_a_single_cars_own_variants_may_share_a_rect(self):
        """clean and fast are the same tyre redrawn - one sprite, no error."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64]),
                     "fast": self.wheel("wheel_a.png", 20, [0, 0, 64, 64]),
                     "damaged": self.wheel("wheel_a.png", 20, [0, 64, 64, 128])},
        })

        specs = carbank_bake.wheel_specs(bank, [])
        carbank_bake._check_wheel_clashes(specs)

        islands = carbank_bake.wheel_islands(bank)
        self.assertEqual(len(islands[20]), 2)


class WheelLumpV2Tests(unittest.TestCase):
    def test_each_slot_carries_its_own_models(self):
        """v2 names the model slot, so two cars' sets do not overwrite each other."""
        geom = carbank_wheels.wheel_geom(20, (0, 0, 63, 63), name="clean")

        body = bake.build_wheel_lump_v2([(0, [geom, None, None]),
                                         (1, [None, geom, geom])])

        magic, nslots = struct.unpack_from("<4si", body, 0)
        self.assertEqual((magic, nslots), (b"WHL2", 2))

        model, count = struct.unpack_from("<ii", body, 8)
        self.assertEqual((model, count), (0, 3))

        offs = list(struct.unpack_from("<iii", body, 16))
        self.assertGreaterEqual(offs[0], 0)
        self.assertEqual(offs[1:], [-1, -1])        # v1's "keep the host wheel"

        # the block really is a wheel MODEL: the eight FT4 quads models.c wants
        wheel = lev1.decode_model(body[offs[0]:], 0)
        self.assertEqual(len(wheel.polys), 8)

        # slot 1's header follows slot 0's blocks, and names model 1
        next_at = offs[0] + len(carbank_wheels.wheel_record(20, (0, 0, 63, 63)))
        model1, count1 = struct.unpack_from("<ii", body, next_at)
        self.assertEqual((model1, count1), (1, 3))
        self.assertEqual(struct.unpack_from("<i", body, next_at + 8)[0], -1)

    def test_a_repeated_slot_is_refused(self):
        geom = carbank_wheels.wheel_geom(20, (0, 0, 63, 63), name="clean")

        with self.assertRaises(SystemExit):
            bake.build_wheel_lump_v2([(0, [geom]), (0, [geom])])


if __name__ == "__main__":
    unittest.main()
