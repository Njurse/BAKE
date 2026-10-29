#!/usr/bin/env python3
"""Tests for bankjson.py -- the `declare` half of bank.json.

Run from this directory:
    python -m unittest test_bankjson -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bankjson


class BankFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bankjson-")
        self.addCleanup(shutil.rmtree, self.dir)

        for name in ("clean.obj", "low.obj", "atlas.png", "body.png",
                     "wheel.png", "engine.wav", "idle.wav", "horn.wav",
                     "siren.wav"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("x")
    def write(self, declare, name="bank.json", computed=None):
        body = dict(computed or {"city": "TEST", "lev": "TEST.LEV",
                                 "lcf": "TEST.LCF", "size": 1})

        if declare is not None:
            body["declare"] = declare

        path = os.path.join(self.dir, name)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f)

        return path

    def car(self, **over):
        data = {"clean": "clean.obj", "low": "low.obj", "atlas": "atlas.png",
                "wheels": {"clean": "wheel.png"}}
        data.update(over)
        return data

    def declare(self, car=None, order=None, **over):
        decl = {"schema": 1, "name": "TEST",
                "order": order or [{"slot": 1, "car": "car1"}],
                "cars": {"car1": car if car is not None else self.car()}}
        decl.update(over)
        return decl


class LoadTests(BankFixture):
    def test_a_donor_bank_has_nothing_declared(self):
        bank = bankjson.load(self.write(None))

        self.assertFalse(bank.declared)
        self.assertEqual(bank.cars, {})
        self.assertEqual(bank.order, [])

    def test_a_full_declaration_loads(self):
        bank = bankjson.load(self.write(self.declare()))

        self.assertTrue(bank.declared)
        self.assertEqual(bank.order, [(1, "car1")])

        car = bank.cars["car1"]
        self.assertTrue(car.clean.endswith("clean.obj"))
        self.assertIsNone(car.damaged)
        self.assertIsNone(car.sounds)

    def test_wheels_may_omit_a_variant(self):
        bank = bankjson.load(self.write(self.declare(
            car=self.car(wheels={"clean": "wheel.png", "fast": "wheel.png"}))))

        self.assertIsNone(bank.cars["car1"].wheels["damaged"])
        self.assertTrue(bank.cars["car1"].wheels["fast"].path.endswith("wheel.png"))

    def test_a_plain_wheel_path_names_no_page(self):
        # back compatible: art without a page cannot become a wheel MODEL, so the
        # bake leaves that variant on the host level's wheel texture
        bank = bankjson.load(self.write(self.declare()))

        self.assertIsNone(bank.cars["car1"].wheels["clean"].set)
        self.assertIsNone(bank.cars["car1"].wheels["clean"].island)

    def test_a_wheel_entry_may_name_its_page_and_crop(self):
        bank = bankjson.load(self.write(self.declare(car=self.car(wheels={
            "clean": {"png": "wheel.png", "set": 2,
                      "island": [0, 0, 60, 60]}}))))

        wheel = bank.cars["car1"].wheels["clean"]

        self.assertTrue(wheel.path.endswith("wheel.png"))
        self.assertEqual(wheel.set, 2)
        self.assertEqual(wheel.island, (0, 0, 60, 60))

    def test_a_wheel_entry_may_name_a_page_without_a_crop(self):
        bank = bankjson.load(self.write(self.declare(car=self.car(wheels={
            "clean": {"png": "wheel.png", "set": 2}}))))

        self.assertEqual(bank.cars["car1"].wheels["clean"].set, 2)
        self.assertIsNone(bank.cars["car1"].wheels["clean"].island)

    def test_a_wheel_rect_needs_no_image(self):
        # this is the shape a declaration read back out of a bank has
        bank = bankjson.load(self.write(self.declare(car=self.car(wheels={
            "clean": {"set": 2, "island": [0, 0, 60, 60]}}))))

        wheel = bank.cars["car1"].wheels["clean"]

        self.assertIsNone(wheel.path)
        self.assertEqual(wheel.set, 2)
        self.assertEqual(wheel.island, (0, 0, 60, 60))


class SoundTests(BankFixture):
    def test_a_civilian_car_needs_a_horn(self):
        bank = bankjson.load(self.write(self.declare(
            car=self.car(sounds={"engine": "engine.wav", "horn": "horn.wav"}))))

        sounds = bank.cars["car1"].sounds

        self.assertFalse(sounds.cop)
        self.assertTrue(sounds.horn["wav"].endswith("horn.wav"))
        self.assertIsNone(sounds.siren)

    def test_a_cop_car_takes_the_siren_and_no_horn(self):
        bank = bankjson.load(self.write(self.declare(
            car=self.car(sounds={"engine": "engine.wav", "siren": "siren.wav",
                                 "cop": True}))))

        sounds = bank.cars["car1"].sounds

        self.assertTrue(sounds.cop)
        self.assertTrue(sounds.siren["wav"].endswith("siren.wav"))
        self.assertIsNone(sounds.horn)

    def test_a_cop_car_with_a_horn_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(sounds={"engine": "engine.wav", "siren": "siren.wav",
                                     "horn": "horn.wav", "cop": True}))))

        self.assertIn("sounds.horn", str(caught.exception))
        self.assertIn("siren", str(caught.exception))

    def test_a_civilian_car_with_a_siren_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(sounds={"engine": "engine.wav", "horn": "horn.wav",
                                     "siren": "siren.wav"}))))

        self.assertIn("sounds.siren", str(caught.exception))

    def test_a_cop_car_without_a_siren_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(sounds={"engine": "engine.wav", "cop": True}))))

        self.assertIn("siren", str(caught.exception))

    def test_a_missing_sample_file_names_the_field(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(sounds={"engine": "nope.wav", "horn": "horn.wav"}))))

        self.assertIn("sounds.engine", str(caught.exception))
        self.assertIn("nope.wav", str(caught.exception))

    def test_the_idle_is_optional(self):
        bank = bankjson.load(self.write(self.declare(
            car=self.car(sounds={"engine": "engine.wav", "horn": "horn.wav"}))))

        self.assertIsNone(bank.cars["car1"].sounds.idle)

    def test_a_bad_pitch_is_refused(self):
        for pct in (5, 500, "loud"):
            with self.assertRaises(bankjson.BankError) as caught:
                bankjson.load(self.write(self.declare(
                    car=self.car(sounds={"engine": "engine.wav",
                                         "horn": "horn.wav",
                                         "engine_pct": pct}))))

            self.assertIn("engine_pct", str(caught.exception))


class RefusalTests(BankFixture):
    def test_an_unknown_key_is_refused(self):
        # the typo must not silently mean "use the donor's"
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(sonds={"engine": "engine.wav"}))))

        self.assertIn("sonds", str(caught.exception))

    def test_clean_is_required(self):
        car = self.car()
        del car["clean"]

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=car)))

        self.assertIn("clean", str(caught.exception))

    def test_a_car_needs_some_texture_source(self):
        car = self.car()
        del car["atlas"]

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=car)))

        self.assertIn("atlas", str(caught.exception))

    def test_a_textures_map_replaces_the_atlas(self):
        car = self.car()
        del car["atlas"]
        car["textures"] = {"1": "body.png", "2": "wheel.png"}

        bank = bankjson.load(self.write(self.declare(car=car)))

        self.assertEqual([t.set for t in bank.cars["car1"].textures], [1, 2])
        self.assertTrue(bank.cars["car1"].textures[0].image.endswith("body.png"))

    def test_a_texture_entry_may_carry_its_bake_options(self):
        car = self.car()
        del car["atlas"]
        car["textures"] = {"2": {"png": "wheel.png", "transparent": "auto",
                                 "transparent_chroma": 128}}

        texture = bankjson.load(self.write(self.declare(car=car)
                                           )).cars["car1"].textures[0]

        self.assertEqual(texture.set, 2)
        self.assertEqual(texture.bake_options()["chroma"], 128)

    def test_declaring_pages_is_refused_with_the_replacement_named(self):
        # a page's png is a bake OUTPUT: declaring it would make the output an
        # input, and re-baking from a preview is generation loss
        car = self.car()
        del car["atlas"]
        car["pages"] = [{"set": 1, "png": "atlas.png"}]

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=car)))

        self.assertIn("TEXTURES", str(caught.exception))
        self.assertIn("preview", str(caught.exception).lower() + "preview")

    def test_a_wheel_crop_must_be_a_rect(self):
        for island in ([0, 0, 0, 60], [0, 0, 60, 60, 60], [0, 0, 60],
                       [0, 0, 257, 60], [-1, 0, 60, 60], "0,0,60,60"):
            with self.assertRaises(bankjson.BankError) as caught:
                bankjson.load(self.write(self.declare(car=self.car(wheels={
                    "clean": {"png": "wheel.png", "set": 2,
                              "island": island}}))))

            self.assertIn("island", str(caught.exception))

    def test_a_wheel_set_must_be_a_set_id(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=self.car(wheels={
                "clean": {"png": "wheel.png", "set": 256}}))))

        self.assertIn("set", str(caught.exception))

    def test_an_unknown_wheel_key_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=self.car(wheels={
                "clean": {"png": "wheel.png", "sett": 2}}))))

        self.assertIn("sett", str(caught.exception))

    def test_a_wheel_entry_without_an_image_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(car=self.car(wheels={
                "clean": {"set": 2}}))))

        self.assertIn("png", str(caught.exception))

    def test_a_slot_out_of_range_is_refused(self):
        for slot in (-1, 13, "1"):
            with self.assertRaises(bankjson.BankError) as caught:
                bankjson.load(self.write(self.declare(
                    order=[{"slot": slot, "car": "car1"}])))

            self.assertIn("slot", str(caught.exception))

    def test_a_duplicate_slot_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                order=[{"slot": 1, "car": "car1"},
                       {"slot": 1, "car": "car1"}])))

        self.assertIn("twice", str(caught.exception))

    def test_an_order_entry_must_name_a_declared_car(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                order=[{"slot": 1, "car": "ghost"}])))

        self.assertIn("ghost", str(caught.exception))

    def test_a_wrong_schema_version_is_refused(self):
        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(schema=2)))

        self.assertIn("schema", str(caught.exception))

    def test_broken_json_names_the_file(self):
        path = os.path.join(self.dir, "bank.json")

        with open(path, "w") as f:
            f.write("{ not json")

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(path)

        self.assertIn("bank.json", str(caught.exception))


class WriteBackTests(BankFixture):
    def test_write_declare_keeps_the_computed_half(self):
        decl = self.declare()
        path = self.write(decl, computed={"city": "FIREBIRD",
                                          "lev": "FIREBIRD.LEV",
                                          "pages": [{"set": 1, "size": 10}]})

        bankjson.write_declare(path, decl)

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        self.assertEqual(raw["city"], "FIREBIRD")
        self.assertEqual(raw["pages"], [{"set": 1, "size": 10}])
        self.assertEqual(raw["declare"], decl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
