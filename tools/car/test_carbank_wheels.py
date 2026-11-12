#!/usr/bin/env python3
"""Tests for per-car wheel sets in a car bank.

A bank used to carry ONE wheel set, owned by the first car in slot order: every
other car's `wheels` block was a warning and never reached the bank. It now
carries a set per car, so two cars may crop different pages - or different rects
of one page - and each keeps its own.

Run from this directory:
    python -m unittest test_carbank_wheels -v
"""

import contextlib
import io
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
import exportd2
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

    def bank(self, cars, textures=None, auto_textures=False):
        """A loaded bank: {car name: wheels dict}, one slot per car, in order."""
        decl_cars = {}

        for name, wheels in cars.items():
            car_dir = os.path.join(self.dir, "cars", name)
            os.makedirs(car_dir, exist_ok=True)

            doc = {"clean": "../../clean.obj",
                   "low": "../../low.obj",
                   "atlas": "../../atlas.png",
                   "wheels": wheels}

            if textures:
                doc["textures"] = dict(textures)

            with open(os.path.join(car_dir, "handling.json"), "w") as f:
                json.dump(doc, f)

            decl_cars[name] = "cars/%s/handling.json" % name

        path = os.path.join(self.dir, "bank.json")
        declare = {"schema": 1, "name": "TEST",
                   "order": [{"slot": i, "car": name}
                             for i, name in enumerate(cars)],
                   "cars": decl_cars}

        if auto_textures:
            declare["auto_textures"] = True

        with open(path, "w") as f:
            json.dump({"city": "TEST", "lev": "TEST.LEV", "lcf": "TEST.LCF",
                       "size": 1, "declare": declare}, f)

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
        carbank_bake._check_wheel_clashes(bank, specs)        # must not raise

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
        carbank_bake._check_wheel_clashes(bank, specs)

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
        carbank_bake._check_wheel_clashes(bank, specs)

    def test_two_cars_on_overlapping_rects_are_relocated(self):
        """Overlap is resolved automatically and the move is reported."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {"clean": self.wheel("wheel_b.png", 20, [32, 32, 96, 96])},
        })
        err = io.StringIO()

        with contextlib.redirect_stderr(err):
            specs = carbank_bake.wheel_specs(bank, [])

        self.assertNotEqual(specs[1]["wheels"][0]["island"], [32, 32, 96, 96])
        self.assertIn("moved from set 20", err.getvalue())
        self.assertIn("overlaps carA wheels.clean", err.getvalue())
        carbank_bake._check_wheel_clashes(bank, specs)

    def test_a_single_cars_own_variants_may_share_a_rect(self):
        """clean and fast are the same tyre redrawn - one sprite, no error."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64]),
                     "fast": self.wheel("wheel_a.png", 20, [0, 0, 64, 64]),
                     "damaged": self.wheel("wheel_a.png", 20, [0, 64, 64, 128])},
        })

        specs = carbank_bake.wheel_specs(bank, [])
        carbank_bake._check_wheel_clashes(bank, specs)

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

        model, count, slot_bytes = struct.unpack_from("<iii", body, 8)
        self.assertEqual((model, count), (0, 3))

        offs = list(struct.unpack_from("<iii", body, 20))
        self.assertGreaterEqual(offs[0], 0)
        self.assertEqual(offs[1:], [-1, -1])        # v1's "keep the host wheel"

        # the block really is a wheel MODEL: the eight FT4 quads models.c wants
        wheel = lev1.decode_model(body[offs[0]:], 0)
        self.assertEqual(len(wheel.polys), 8)

        # the slot's byte length is what lets a reader step to the next slot,
        # because a MODEL block is variable-length
        next_at = 8 + slot_bytes
        model1, count1, slot1_bytes = struct.unpack_from("<iii", body, next_at)
        self.assertEqual((model1, count1), (1, 3))
        self.assertEqual(struct.unpack_from("<i", body, next_at + 12)[0], -1)
        self.assertEqual(next_at + slot1_bytes, len(body))

    def test_a_repeated_slot_is_refused(self):
        geom = carbank_wheels.wheel_geom(20, (0, 0, 63, 63), name="clean")

        with self.assertRaises(SystemExit):
            bake.build_wheel_lump_v2([(0, [geom]), (0, [geom])])


class WheelSegmentVerificationTests(unittest.TestCase):
    """The verifier must read the segment the bake actually wrote."""

    ISLANDS = {
        "clean": [192, 64, 256, 128],
        "damaged": [192, 0, 256, 64],
        "fast": [192, 64, 256, 128],
    }

    def specs(self, setid):
        return [{"variant": variant, "set": setid, "island": list(island)}
                for variant, island in self.ISLANDS.items()]

    def bank(self, car_wheels):
        body = exportd2.build_wheel_body_v2(car_wheels)
        return exportd2.assemble_bank(
            b"model-body", b"texinfo-body", b"pallet-body",
            [b"\x00" * 2048], b"texture-names", b"model-names",
            wheels2_body=body)

    def test_a_v2_bank_verifies_against_its_per_model_sets(self):
        """DEMOCARS' shape: model 0 on set 20, model 1 on set 4."""
        car_wheels = [
            {"model": 0, "car": "DEMOHAVANA_model0", "inherited": False,
             "wheels": self.specs(20)},
            {"model": 1, "car": "VEGAS_model1", "inherited": False,
             "wheels": self.specs(4)},
        ]

        blob = self.bank(car_wheels)
        problems = exportd2.verify_wheels_v2(blob, car_wheels)

        self.assertEqual(problems, [])
        self.assertEqual(exportd2.d2_lump(blob, exportd2.LUMP_CAR_WHEELS),
                         None, "a two-set bank must not also write v1")

    def test_a_wrong_page_is_reported_by_model(self):
        car_wheels = [
            {"model": 0, "car": "carA", "inherited": False,
             "wheels": self.specs(20)},
            {"model": 1, "car": "carB", "inherited": False,
             "wheels": self.specs(4)},
        ]
        blob = self.bank(car_wheels)
        car_wheels[1]["wheels"][0]["set"] = 21

        problems = exportd2.verify_wheels_v2(blob, car_wheels)

        self.assertTrue(any("model 1 wheel clean" in p and "page set 4" in p
                            and "declared 21" in p for p in problems),
                        problems)

    def test_a_declared_wheel_written_as_minus_one_is_reported(self):
        specs = self.specs(20)
        specs[2] = None                     # the segment writes -1 for fast
        car_wheels = [
            {"model": 0, "car": "carA", "inherited": False,
             "wheels": specs},
        ]
        blob = self.bank(car_wheels)
        car_wheels[0]["wheels"][2] = {"variant": "fast", "set": 20,
                                      "island": [0, 0, 64, 64]}

        problems = exportd2.verify_wheels_v2(blob, car_wheels)

        self.assertTrue(any("model 0 wheel fast" in p and "-1" in p
                            for p in problems), problems)

    def test_a_missing_v2_segment_is_reported(self):
        car_wheels = [
            {"model": 0, "car": "carA", "inherited": False,
             "wheels": self.specs(20)},
        ]
        blob = exportd2.assemble_bank(
            b"model-body", b"texinfo-body", b"pallet-body",
            [b"\x00" * 2048], b"texture-names", b"model-names")

        problems = exportd2.verify_wheels_v2(blob, car_wheels)

        self.assertEqual(problems,
                         ["the bank has no per-model wheel segment "
                          "(1 car(s) declared)"])

    def test_v1_verification_still_reads_lump_200(self):
        specs = self.specs(20)
        body = exportd2.build_wheel_body(specs)
        blob = exportd2.assemble_bank(
            b"model-body", b"texinfo-body", b"pallet-body",
            [b"\x00" * 2048], b"texture-names", b"model-names",
            wheels_body=body)

        self.assertEqual(exportd2.verify_wheels(blob, specs), [])


    def test_wheel_geoms_carry_the_assigned_texid(self):
        specs = [{"variant": "clean", "set": 20,
                  "island": [192, 64, 256, 128], "texid": 27}]

        geoms = exportd2._wheel_geoms(specs)

        self.assertEqual(geoms[0]["polys"][2]["id"], 27)


class BodyClashTests(WheelFixture):
    def test_a_wheel_on_body_art_is_refused(self):
        """A wheel island over the body's UV islands would repaint the skin."""
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
        })
        specs = carbank_bake.wheel_specs(bank, [])

        # the fixture OBJs carry no geometry, so stand in for a page that does
        real = carbank_bake._uv_islands
        carbank_bake._uv_islands = lambda _bank, _set: {
            7: [[(10, 10), (40, 10), (40, 40), (10, 40)]]}

        try:
            with self.assertRaises(SystemExit) as caught:
                carbank_bake._check_wheel_clashes(bank, specs)
        finally:
            carbank_bake._uv_islands = real

        message = str(caught.exception)
        self.assertIn("overlaps the body art", message)
        self.assertIn("carA", message)
        self.assertIn("page set 20", message)


class AutoPlacementTests(WheelFixture):
    """A "auto" island: placed by the bake, deterministic, and never ambiguous."""

    def setUp(self):
        super().setUp()

        from PIL import Image

        for name in ("wheel_a.png", "wheel_b.png"):
            Image.new("RGBA", (64, 64), (200, 40, 40, 255)).save(
                os.path.join(self.dir, name))

    def auto(self, name="wheel_a.png", setid=20):
        return {"png": "../../" + name, "set": setid, "island": "auto"}

    def test_the_placer_is_deterministic(self):
        """The same input twice lands on the same rect (bake-level determinism,
        identical rects and identical LEVs across two bakes, is covered end to
        end on a real bank copy)."""
        bank = self.bank({"carA": {"clean": self.auto()}})
        path = bank.cars["carA"].wheels["clean"].path

        first = carbank_bake._auto_island(bank, 20, [], path, "carA", "clean", [])
        second = carbank_bake._auto_island(bank, 20, [], path, "carA", "clean", [])

        self.assertEqual(first, second)
        self.assertEqual(first[0], 20)
        self.assertEqual(first[1], [0, 0, 64, 64])

    def test_auto_placement_resolves_to_a_concrete_rect(self):
        """\"auto\" comes back as a rect, and two variants never share one."""
        cars = {"carA": {"clean": self.auto(), "damaged": self.auto()}}

        specs = carbank_bake.wheel_specs(self.bank(cars), [])[0]["wheels"]

        self.assertIsNotNone(specs, "no specs came back at all")
        self.assertEqual(specs[0]["island"], [0, 0, 64, 64])
        self.assertIsInstance(specs[1]["island"], list)
        self.assertNotEqual(specs[0]["island"], specs[1]["island"])

    def test_clean_and_fast_share_the_assigned_texid(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20,
                                          [192, 64, 256, 128]),
                     "fast": self.wheel("wheel_a.png", 20,
                                        [192, 64, 256, 128])},
        }, textures={"20": {"png": "../../atlas.png"}})
        wheel_tex = {(20, (192, 64, 256, 128)): 27}

        specs = carbank_bake.wheel_specs(bank, [], wheel_tex)[0]["wheels"]

        self.assertEqual(specs[0]["texid"], 27)
        self.assertEqual(specs[2]["texid"], 27)

    def test_a_full_page_moves_the_sprite_and_reports_it(self):
        bank = self.bank({"carA": {"clean": self.auto()}},
                         textures={"20": {"png": "../../atlas.png"},
                                   "21": {"png": "../../atlas.png"}})
        real = carbank_bake._page_free

        def fake(_bank, setid, car_wheels=(), width=0, height=0,
                 grid=carbank_bake.WHEEL_GRID, limit=8):
            return [] if setid == 20 else [(0, 0, width, height)]

        carbank_bake._page_free = fake
        err = io.StringIO()

        try:
            with contextlib.redirect_stderr(err):
                specs = carbank_bake.wheel_specs(bank, [])
        finally:
            carbank_bake._page_free = real

        self.assertEqual(specs[0]["wheels"][0]["set"], 21)
        self.assertIn("moved to page set 21", err.getvalue())

    def test_a_bank_with_no_room_stops_with_an_inventory(self):
        bank = self.bank({"carA": {"clean": self.auto()}},
                         textures={"20": {"png": "../../atlas.png"}})
        real = carbank_bake._page_free
        carbank_bake._page_free = lambda *a, **k: []

        try:
            with self.assertRaises(SystemExit) as caught:
                carbank_bake.wheel_specs(bank, [])
        finally:
            carbank_bake._page_free = real

        message = str(caught.exception)
        self.assertIn("has no room in this bank", message)
        self.assertIn("set 20", message)
        self.assertIn("largest free square", message)

    def test_auto_textures_keeps_a_valid_declared_island(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20,
                                          [192, 64, 256, 128])},
        }, textures={"20": {"png": "../../atlas.png"}}, auto_textures=True)

        specs = carbank_bake.wheel_specs(bank, [])

        self.assertEqual(specs[0]["wheels"][0]["island"], [192, 64, 256, 128])

    def test_auto_textures_moves_a_declared_island_off_body_art(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
        }, textures={"20": {"png": "../../atlas.png"}}, auto_textures=True)

        real = carbank_bake._uv_islands
        carbank_bake._uv_islands = lambda _bank, _set: {
            7: [[(10, 10), (40, 10), (40, 40), (10, 40)]]}
        err = io.StringIO()

        try:
            with contextlib.redirect_stderr(err):
                specs = carbank_bake.wheel_specs(bank, [])
        finally:
            carbank_bake._uv_islands = real

        self.assertNotEqual(specs[0]["wheels"][0]["island"], [0, 0, 64, 64])
        self.assertIn("moved from set 20", err.getvalue())
        self.assertIn("overlaps the body art", err.getvalue())

    def test_auto_textures_no_room_keeps_the_inventory_error(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
        }, textures={"20": {"png": "../../atlas.png"}}, auto_textures=True)
        real_uv = carbank_bake._uv_islands
        real_free = carbank_bake._page_free
        carbank_bake._uv_islands = lambda _bank, _set: {
            7: [[(10, 10), (40, 10), (40, 40), (10, 40)]]}
        carbank_bake._page_free = lambda *a, **k: []

        try:
            with self.assertRaises(SystemExit) as caught:
                carbank_bake.wheel_specs(bank, [])
        finally:
            carbank_bake._uv_islands = real_uv
            carbank_bake._page_free = real_free

        message = str(caught.exception)
        self.assertIn("has no room in this bank", message)
        self.assertIn("largest free square", message)

    def test_auto_textures_moves_a_declared_island_off_another_car(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
            "carB": {"clean": self.wheel("wheel_b.png", 20, [0, 0, 64, 64])},
        }, textures={"20": {"png": "../../atlas.png"}}, auto_textures=True)
        err = io.StringIO()

        with contextlib.redirect_stderr(err):
            specs = carbank_bake.wheel_specs(bank, [])

        self.assertEqual(specs[0]["wheels"][0]["island"], [0, 0, 64, 64])
        self.assertNotEqual(specs[1]["wheels"][0]["island"], [0, 0, 64, 64])
        self.assertIn("overlaps carA wheels.clean", err.getvalue())

    def test_an_island_that_overflows_the_page_is_reported_unusable(self):
        # bankjson refuses an out-of-page island at load time; _island_usable
        # keeps the same rule for a spec assembled by hand.
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
        }, textures={"20": {"png": "../../atlas.png"}})

        reason = carbank_bake._island_usable(bank, 20, [], "carA",
                                             [240, 0, 304, 64])

        self.assertEqual(reason, "it does not fit one 256x256 page")

    def test_a_body_clash_is_relocated_by_default(self):
        bank = self.bank({
            "carA": {"clean": self.wheel("wheel_a.png", 20, [0, 0, 64, 64])},
        }, textures={"20": {"png": "../../atlas.png"}})
        real = carbank_bake._uv_islands
        carbank_bake._uv_islands = lambda _bank, _set: {
            7: [[(10, 10), (40, 10), (40, 40), (10, 40)]]}
        err = io.StringIO()

        try:
            with contextlib.redirect_stderr(err):
                specs = carbank_bake.wheel_specs(bank, [])

            self.assertNotEqual(specs[0]["wheels"][0]["island"], [0, 0, 64, 64])
            carbank_bake._check_wheel_clashes(bank, specs)
        finally:
            carbank_bake._uv_islands = real

        self.assertIn("overlaps the body art", err.getvalue())


class DeclarationGuardTests(WheelFixture):
    def test_a_duplicated_key_is_refused(self):
        """Two "order" arrays silently drop the car the first names."""
        path = os.path.join(self.dir, "bank.json")

        with open(path, "w") as f:
            f.write('{"city": "T", "declare": {"schema": 1, "name": "T",'
                    ' "order": [{"slot": 0, "car": "carA"}],'
                    ' "order": [{"slot": 1, "car": "carB"}], "cars": {}}}')

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(path)

        message = str(caught.exception)
        self.assertIn("listed twice", message)
        self.assertIn("order", message)

    def test_a_bank_with_no_wheels_is_not_a_crash(self):
        """No car declaring wheels leaves wheels: None - the checks must cope."""
        bank = self.bank({"carA": {}})

        specs = carbank_bake.wheel_specs(bank, [])
        carbank_bake._check_wheel_clashes(bank, specs)


if __name__ == "__main__":
    unittest.main()
