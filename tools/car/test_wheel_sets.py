#!/usr/bin/env python3
"""Per-car wheel models (lump 200 extension) and colliding page-set remaps.

    python -m unittest test_wheel_sets -v
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
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import bankjson
import carbank_bake
import exportd2
import lev1


def _spec(variant, setid, island=(0, 0, 32, 32)):
    return {"variant": variant, "set": setid, "island": list(island)}


class WheelExtensionTests(unittest.TestCase):
    """The v1 header stays the bank default. Later cars live in the trailer."""

    def test_v1_prefix_matches_todays_default_and_slots_name_both_sets(self):
        default = [_spec("clean", 2), None, _spec("fast", 3, (0, 0, 16, 16))]
        per = [[None, None, None] for _ in range(exportd2.MAX_CAR_MODELS)]
        # slot 0 is the default car: its offsets point at the v1 blocks
        per[0] = list(default)
        # slot 4 is a second car on a different wheel page
        per[4] = [_spec("clean", 9), _spec("damaged", 10, (8, 0, 40, 32)),
                  None]
        # slot 2 declares no wheels

        today = exportd2.build_wheel_body(default)
        both = exportd2.build_wheel_body(default, per)

        self.assertEqual(both[:len(today)], today,
                         "the v1 header and its model blocks changed")
        self.assertEqual(struct.unpack_from("<i", both, 0)[0], 3)
        self.assertEqual(struct.unpack_from("<3i", both, 4),
                         struct.unpack_from("<3i", today, 4))

        ext = exportd2.parse_wheel_extension(both)
        self.assertEqual(ext["version"], 1)
        self.assertEqual(ext["slots"], 13)
        self.assertEqual(ext["at"], len(today))
        self.assertEqual(both[ext["at"]:ext["at"] + 4], b"WHLX")

        v1 = struct.unpack_from("<3i", today, 4)
        self.assertEqual(tuple(ext["offsets"][0]), v1)
        self.assertEqual(ext["offsets"][2], [-1, -1, -1])
        self.assertEqual(ext["offsets"][1], [-1, -1, -1])
        self.assertEqual(ext["offsets"][4][2], -1)
        self.assertGreater(ext["offsets"][4][0], ext["at"])
        self.assertGreater(ext["offsets"][4][1], ext["at"])
        self.assertNotEqual(ext["offsets"][4][0], ext["offsets"][4][1])

        clean = lev1.decode_model(both, ext["offsets"][4][0])
        damaged = lev1.decode_model(both, ext["offsets"][4][1])
        owned = lev1.decode_model(both, ext["offsets"][0][0])

        self.assertEqual(clean.polys[2].texture_set, 9)
        self.assertEqual(damaged.polys[2].texture_set, 10)
        self.assertEqual(owned.polys[2].texture_set, 2)
        self.assertTrue(8 <= len(clean.polys) <= 24)
        self.assertTrue(all(p.kind == "FT4" for p in clean.polys[:8]))

    def test_omitting_the_extension_is_still_the_v1_body(self):
        default = [_spec("clean", 2), None, None]
        self.assertEqual(exportd2.build_wheel_body(default),
                         exportd2.build_wheel_body(default, None))
        self.assertIsNone(exportd2.parse_wheel_extension(
            exportd2.build_wheel_body(default)))


class PageSetRemapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="wheelsets-")
        self.addCleanup(shutil.rmtree, self.dir)

    def _touch(self, name, body="x"):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w") as handle:
            handle.write(body)

        return name

    def _obj(self):
        return self._touch("clean.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
                                         "usemtl set1_id0\nf 1 2 3\n")

    def _bank(self, cars, order):
        self._obj()
        body = {"city": "T", "lev": "T.LEV", "lcf": "T.LCF",
                "declare": {"schema": 1, "name": "T", "order": order,
                            "cars": cars}}
        path = os.path.join(self.dir, "bank.json")

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(body, handle)

        return bankjson.load(path)

    def _car(self, textures, wheels=None):
        data = {"clean": "clean.obj", "textures": textures}

        if wheels is not None:
            data["wheels"] = wheels

        return data

    def test_identical_declarations_share_a_set(self):
        page = self._touch("body.png")
        bank = self._bank({
            "a": self._car({"1": page}),
            "b": self._car({"1": page}),
        }, [{"slot": 0, "car": "a"}, {"slot": 3, "car": "b"}])
        warnings = []
        plan = carbank_bake.plan_page_sets(bank, warnings)

        self.assertEqual(plan.remaps, [])
        self.assertEqual(warnings, [])
        self.assertEqual(plan.by_car["a"][1], 1)
        self.assertEqual(plan.by_car["b"][1], 1)
        self.assertEqual([job["set"] for job in plan.pages], [1])

    def test_a_colliding_body_and_wheel_page_is_remapped(self):
        body = self._touch("body.png")
        wheel = self._touch("wheel.png")
        page2 = self._touch("page2.png")
        # model0 keeps set 1 (body) and set 2 (its wheel page). model3's body
        # and its wheel both name set 1, and the image is not model0's.
        bank = self._bank({
            "CIVICOPS_model0": self._car(
                {"1": body, "2": page2},
                {"clean": {"set": 2, "island": [0, 0, 32, 32]}}),
            "CIVICOPS_model3": self._car(
                {"1": wheel},
                {"clean": {"set": 1, "island": [0, 0, 32, 32]}}),
        }, [{"slot": 0, "car": "CIVICOPS_model0"},
            {"slot": 3, "car": "CIVICOPS_model3"}])
        warnings = []
        plan = carbank_bake.plan_page_sets(bank, warnings)
        moved = [r for r in plan.remaps if r["car"] == "CIVICOPS_model3"]

        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["from"], 1)
        self.assertEqual(moved[0]["to"], 3)
        self.assertEqual(moved[0]["kept_by"], "CIVICOPS_model0")
        self.assertFalse(moved[0]["same"])
        self.assertEqual(plan.by_car["CIVICOPS_model0"][1], 1)
        self.assertEqual(plan.by_car["CIVICOPS_model0"][2], 2)
        self.assertEqual(plan.by_car["CIVICOPS_model3"][1], 3)
        self.assertTrue(any("remapped CIVICOPS_model3 1 -> 3" in w
                            for w in warnings))

        default, by_slot = carbank_bake.wheel_specs(bank, [], plan.by_car)

        self.assertEqual(default[0]["set"], 2)
        self.assertIsNone(default[1])
        self.assertEqual(by_slot[0][0]["set"], 2)
        self.assertEqual(by_slot[3][0]["set"], 3)
        self.assertEqual(by_slot[1], [None, None, None])


class BakedBankTests(unittest.TestCase):
    """Two cars, two wheel pages, through the real bake."""

    def test_both_wheel_sets_land_in_the_extension_and_the_report(self):
        from PIL import Image

        folder = tempfile.mkdtemp(prefix="wheelbake-")
        self.addCleanup(shutil.rmtree, folder)

        def png(name, colour):
            path = os.path.join(folder, name)
            Image.new("RGB", (256, 256), colour).save(path)
            return name

        obj = os.path.join(folder, "clean.obj")

        with open(obj, "w") as handle:
            handle.write("v 0 0 0\nv 80 0 0\nv 0 40 0\n"
                         "vt 0 0\nvt 1 0\nvt 0 1\n"
                         "usemtl set1_id0\n"
                         "f 1/1 2/2 3/3\n")

        body_a = png("body_a.png", (180, 40, 40))
        body_b = png("body_b.png", (40, 40, 180))
        wheel_a = png("wheel_a.png", (30, 30, 30))
        wheel_b = png("wheel_b.png", (220, 220, 80))
        island = [0, 0, 32, 32]
        bank = {
            "city": "TWO", "lev": "TWO.LEV", "lcf": "TWO.LCF",
            "declare": {
                "schema": 1, "name": "TWO", "scale": 1,
                "order": [
                    {"slot": 0, "car": "alpha"},
                    {"slot": 2, "car": "plain"},
                    {"slot": 4, "car": "beta"},
                ],
                "cars": {
                    "alpha": {"clean": "clean.obj",
                              "textures": {"1": body_a, "2": wheel_a},
                              "wheels": {"clean": {"set": 2, "island": island}}},
                    "plain": {"clean": "clean.obj",
                              "textures": {"1": body_a}},
                    "beta": {"clean": "clean.obj",
                             "textures": {"1": body_b, "2": wheel_b},
                             "wheels": {"clean": {"set": 2, "island": island}}},
                },
            },
        }
        path = os.path.join(folder, "bank.json")

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(bank, handle)

        ok, warnings = carbank_bake.bake_bank(path, folder, scale=1)
        self.assertTrue(ok, warnings)
        self.assertTrue(any("remapped beta 1 -> 3" in w for w in warnings))
        self.assertTrue(any("remapped beta 2 -> 4" in w for w in warnings))
        self.assertFalse(any("declares wheels too" in w for w in warnings))

        report_path = os.path.join(folder, "TWO.report.json")

        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)

        by_car = {row["car"]: row for row in report["model_wheels"]}
        self.assertEqual(by_car["alpha"]["slot"], 0)
        self.assertEqual(by_car["alpha"]["clean"]["set"], 2)
        self.assertIsNone(by_car["alpha"]["damaged"])
        self.assertIsNone(by_car["plain"]["clean"])
        self.assertEqual(by_car["beta"]["slot"], 4)
        self.assertEqual(by_car["beta"]["clean"]["set"], 4)
        moved = {(r["car"], r["from"], r["to"]) for r in report["page_set_remaps"]}
        self.assertIn(("beta", 1, 3), moved)
        self.assertIn(("beta", 2, 4), moved)
        self.assertNotIn(("plain", 1, 1), {(r["car"], r["from"], r["to"])
                                           for r in report["page_set_remaps"]})
        self.assertIn("1..127", report["page_set_range"]["assumption"])

        with open(os.path.join(folder, "TWO.LEV"), "rb") as handle:
            blob = handle.read()

        body, size = exportd2.d2_lump(blob, exportd2.LUMP_CAR_WHEELS)
        segment = blob[body:body + size]

        with open(os.path.join(folder, "TWO.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)

        default = exportd2._load_wheels(manifest)
        today = exportd2.build_wheel_body(default)
        self.assertEqual(segment[:len(today)], today)
        self.assertEqual(default[0]["set"], 2)

        ext = exportd2.parse_wheel_extension(segment)
        v1 = struct.unpack_from("<3i", segment, 4)
        self.assertEqual(tuple(ext["offsets"][0]), v1)
        self.assertEqual(ext["offsets"][2], [-1, -1, -1])
        self.assertEqual(ext["offsets"][4][1], -1)
        self.assertEqual(ext["offsets"][4][2], -1)

        beta = lev1.decode_model(segment, ext["offsets"][4][0])
        alpha = lev1.decode_model(segment, ext["offsets"][0][0])
        self.assertEqual(beta.polys[2].texture_set, 4)
        self.assertEqual(alpha.polys[2].texture_set, 2)
        self.assertNotEqual(ext["offsets"][4][0], ext["offsets"][0][0])

        table = exportd2.d2_model_table(blob)
        alpha_body = exportd2.decode_variants(blob, table, 0)["clean"]
        beta_body = exportd2.decode_variants(blob, table, 4)["clean"]
        plain_body = exportd2.decode_variants(blob, table, 2)["clean"]
        self.assertEqual({p.texture_set for p in alpha_body.polys}, {1})
        self.assertEqual({p.texture_set for p in plain_body.polys}, {1})
        self.assertEqual({p.texture_set for p in beta_body.polys}, {3})
        self.assertEqual(exportd2.verify_wheels(
            blob, default, exportd2._load_wheel_slots(manifest)), [])


class PreviewWheelTests(unittest.TestCase):
    def test_each_car_shows_its_own_wheels_and_a_bare_car_shows_the_default(self):
        import preview_car

        bank = {"declare": {
            "order": [{"slot": 0, "car": "alpha"},
                      {"slot": 4, "car": "beta"},
                      {"slot": 2, "car": "plain"}],
            "cars": {
                "alpha": {"wheels": {"clean": {"png": "a.png", "set": 2}}},
                "beta": {"wheels": {"clean": {"png": "b.png", "set": 9}}},
                "plain": {"clean": "plain.obj"},
            },
        }}
        _root, car = preview_car.preview_wheel_car("/tmp", bank, "beta")
        self.assertEqual(car["wheels"]["clean"]["png"], "b.png")
        _root, car = preview_car.preview_wheel_car("/tmp", bank, "plain")
        self.assertEqual(car["wheels"]["clean"]["png"], "a.png")
        _root, car = preview_car.preview_wheel_car("/tmp", bank, "alpha")
        self.assertEqual(car["wheels"]["clean"]["png"], "a.png")


if __name__ == "__main__":
    unittest.main()
