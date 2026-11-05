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
    def test_a_legacy_bank_has_nothing_declared(self):
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


class PortraitTests(BankFixture):
    def _page(self, name="portrait.raw", clut=False, clut_size=32):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"\x11" * bankjson.PORTRAIT_BYTES)
        if clut:
            with open(os.path.splitext(path)[0] + ".clut", "wb") as f:
                f.write(b"\x00\x80" * (clut_size // 2))
        return name

    def test_a_portrait_page_loads_and_installs_by_slot(self):
        self._page(clut=True)
        bank = bankjson.load(self.write(self.declare(
            car=self.car(portrait="portrait.raw"),
            order=[{"slot": 3, "car": "car1"}]),
            computed={"city": "FIREBIRD", "lev": "build/FIREBIRD.LEV"}))

        car = bank.cars["car1"]
        self.assertTrue(car.portrait.endswith("portrait.raw"))
        self.assertTrue(car.portrait_clut.endswith("portrait.clut"))

        out = os.path.join(self.dir, "build")
        slots, notes = bankjson.install_portraits(bank, out, "FIREBIRD")

        self.assertEqual(slots, [3])
        self.assertEqual(notes, [])
        raw = os.path.join(out, "FIREBIRD.portraits", "3.raw")
        clut = os.path.join(out, "FIREBIRD.portraits", "3.clut")
        self.assertEqual(os.path.getsize(raw), bankjson.PORTRAIT_BYTES)
        self.assertEqual(os.path.getsize(clut), bankjson.PORTRAIT_CLUT_BYTES)

        packed = os.path.join(out, "FIREBIRD.RAW")
        with open(packed, "rb") as f:
            blob = f.read()
        page = bankjson.PORTRAIT_BYTES
        # slot 3, not a dense one-page file: models 0..2 are empty pages
        self.assertEqual(len(blob), 4 * page)
        self.assertEqual(blob[:3 * page], b"\x00" * (3 * page))
        self.assertEqual(blob[3 * page:], b"\x11" * page)

    def test_a_short_portrait_is_refused(self):
        with open(os.path.join(self.dir, "portrait.raw"), "wb") as f:
            f.write(b"\x00" * 100)

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(portrait="portrait.raw"))))

        self.assertIn("32768", str(caught.exception))

    def test_a_short_clut_is_refused(self):
        self._page(clut=True, clut_size=8)

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(portrait="portrait.raw"))))

        self.assertIn("32", str(caught.exception))

    def test_a_portrait_under_build_is_refused(self):
        os.makedirs(os.path.join(self.dir, "build"))
        with open(os.path.join(self.dir, "build", "portrait.raw"), "wb") as f:
            f.write(b"\x11" * bankjson.PORTRAIT_BYTES)

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(portrait="build/portrait.raw"))))

        self.assertIn("build/", str(caught.exception))

    def test_a_portrait_on_an_unordered_car_is_noted(self):
        self._page()
        # car1 is ordered and has no portrait; car2 has a page but no slot
        decl = self.declare(car=self.car())
        decl["cars"]["car2"] = self.car(portrait="portrait.raw")
        bank = bankjson.load(self.write(decl))
        slots, notes = bankjson.install_portraits(bank, self.dir, "TEST")

        self.assertEqual(slots, [])
        self.assertIn("car2", notes[0])
        self.assertFalse(os.path.isdir(os.path.join(self.dir, "TEST.portraits")))
        self.assertFalse(os.path.isfile(os.path.join(self.dir, "TEST.RAW")))

    def test_sparse_model_slots_pack_at_those_offsets(self):
        # Miami's cop is model 1 (roster slot 0) and model 8 is roster slot 4.
        # The RAW is indexed by the model, so these two pages are not adjacent.
        self._page("cop.raw")
        with open(os.path.join(self.dir, "civilian.raw"), "wb") as f:
            f.write(b"\x22" * bankjson.PORTRAIT_BYTES)
        decl = self.declare(
            car=self.car(portrait="cop.raw"),
            order=[{"slot": 1, "car": "car1"}, {"slot": 8, "car": "car2"}])
        decl["cars"]["car2"] = self.car(portrait="civilian.raw")
        bank = bankjson.load(self.write(decl))
        slots, notes = bankjson.install_portraits(bank, self.dir, "MIAMI")

        self.assertEqual(sorted(slots), [1, 8])
        self.assertEqual(notes, [])
        page = bankjson.PORTRAIT_BYTES
        with open(os.path.join(self.dir, "MIAMI.RAW"), "rb") as f:
            blob = f.read()

        self.assertEqual(len(blob), 9 * page)
        self.assertEqual(blob[0:page], b"\x00" * page)
        self.assertEqual(blob[1 * page:2 * page], b"\x11" * page)
        self.assertEqual(blob[2 * page:8 * page], b"\x00" * (6 * page))
        self.assertEqual(blob[8 * page:9 * page], b"\x22" * page)

    def test_a_handling_json_portrait_is_relative_to_the_car(self):
        car_dir = os.path.join(self.dir, "cars", "car1", "art")
        os.makedirs(car_dir)
        for name in ("clean.obj", "low.obj", "atlas.png"):
            with open(os.path.join(self.dir, "cars", "car1", name), "w") as f:
                f.write("x")
        with open(os.path.join(car_dir, "icon.raw"), "wb") as f:
            f.write(b"\x5A" * bankjson.PORTRAIT_BYTES)
        with open(os.path.join(car_dir, "icon.clut"), "wb") as f:
            f.write(b"\x34\x12" * 16)
        with open(os.path.join(self.dir, "cars", "car1", "handling.json"),
                  "w", encoding="utf-8") as f:
            json.dump({
                "clean": "clean.obj",
                "low": "low.obj",
                "atlas": "atlas.png",
                "portrait": "art/icon.raw",
                "handling": {"mass": 4096},
                "notes": "ignored by the bake",
            }, f)

        decl = self.declare(order=[{"slot": 8, "car": "car1"}])
        decl["cars"]["car1"] = "cars/car1/handling.json"
        bank = bankjson.load(self.write(decl))
        car = bank.cars["car1"]

        self.assertTrue(car.portrait.endswith(os.path.join("car1", "art", "icon.raw")))
        self.assertTrue(car.portrait_clut.endswith("icon.clut"))
        slots, notes = bankjson.install_portraits(bank, self.dir, "MIAMI")

        self.assertEqual(slots, [8])
        self.assertEqual(notes, [])
        page = bankjson.PORTRAIT_BYTES
        with open(os.path.join(self.dir, "MIAMI.RAW"), "rb") as f:
            blob = f.read()
        self.assertEqual(blob[8 * page:9 * page], b"\x5A" * page)
        self.assertEqual(os.path.getsize(
            os.path.join(self.dir, "MIAMI.portraits", "8.clut")),
            bankjson.PORTRAIT_CLUT_BYTES)

    def _matched_png(self, name, indices, size=(256, 219)):
        """A mode-P PNG whose first 16 colours are the CARBACK CLUT.

        The low 3 bits are dirty on purpose: a PNG saver keeps them, and the
        match is on the 5-bit colour. Index 83 is a second near-black.
        """
        from PIL import Image

        import portrait

        clut, _raw = portrait.portrait_clut()
        pal = []

        for rgb in clut:
            pal.extend(min(255, c + 1) for c in rgb)

        pal.extend([0] * (768 - len(pal)))
        pal[83 * 3:83 * 3 + 3] = (1, 2, 3)
        im = Image.new("P", size)
        im.putpalette(pal)
        im.putdata(list(indices))
        path = os.path.join(self.dir, name)
        parent = os.path.dirname(path)

        if parent and not os.path.isdir(parent):
            os.makedirs(parent)

        im.save(path, format="PNG")
        return name

    def test_a_matched_png_packs_indices_into_the_slot(self):
        import portrait
        import fourbpp

        # Entries 2 and 3 are the same RGB555 colour. Rematch would keep 2.
        # Index 83's low nibble is 3 (that olive); the fold must be black.
        indices = [3, 7, 83] + [0] * (portrait.PAGE_W * portrait.SHOWN_H - 3)
        self._matched_png("cop.png", indices)
        with open(os.path.join(self.dir, "cop.clut"), "wb") as f:
            f.write(b"\xFF" * 32)
        stale_dir = os.path.join(self.dir, "MIAMI.portraits")
        os.makedirs(stale_dir)
        with open(os.path.join(stale_dir, "1.clut"), "wb") as f:
            f.write(b"\xEE" * 32)

        bank = bankjson.load(self.write(self.declare(
            car=self.car(portrait="cop.png"),
            order=[{"slot": 1, "car": "car1"}])))
        car = bank.cars["car1"]

        self.assertTrue(car.portrait.endswith("cop.png"))
        self.assertIsNone(car.portrait_clut)
        self.assertEqual(len(car.portrait_page), bankjson.PORTRAIT_BYTES)
        slots, notes = bankjson.install_portraits(bank, self.dir, "MIAMI")

        self.assertEqual(slots, [1])
        self.assertEqual(notes, [])
        self.assertFalse(os.path.exists(
            os.path.join(self.dir, "MIAMI.portraits", "1.clut")))

        expected = [3, 7, 0] + [0] * (fourbpp.PAGE_W * fourbpp.PAGE_H - 3)
        page = fourbpp.encode_indices(bytes(expected))
        raw = os.path.join(self.dir, "MIAMI.portraits", "1.raw")

        with open(raw, "rb") as f:
            loose = f.read()

        self.assertEqual(loose, page)

        with open(os.path.join(self.dir, "MIAMI.RAW"), "rb") as f:
            blob = f.read()

        page_bytes = bankjson.PORTRAIT_BYTES
        self.assertEqual(blob[:page_bytes], b"\x00" * page_bytes)
        self.assertEqual(blob[page_bytes:2 * page_bytes], page)

    def test_a_handling_json_png_is_relative_to_the_car(self):
        import portrait

        car_dir = os.path.join(self.dir, "cars", "car1")
        os.makedirs(car_dir)

        for name in ("clean.obj", "low.obj", "atlas.png"):
            with open(os.path.join(car_dir, name), "w") as f:
                f.write("x")

        indices = [10] * (portrait.PAGE_W * portrait.SHOWN_H)
        self._matched_png(os.path.join("cars", "car1", "art", "cop.png"),
                          indices)

        with open(os.path.join(car_dir, "handling.json"), "w",
                  encoding="utf-8") as f:
            json.dump({
                "clean": "clean.obj",
                "low": "low.obj",
                "atlas": "atlas.png",
                "portrait": "art/cop.png",
            }, f)

        decl = self.declare(order=[{"slot": 1, "car": "car1"}])
        decl["cars"]["car1"] = "cars/car1/handling.json"
        bank = bankjson.load(self.write(decl))
        car = bank.cars["car1"]

        self.assertTrue(car.portrait.endswith(
            os.path.join("car1", "art", "cop.png")))
        slots, notes = bankjson.install_portraits(bank, self.dir, "MIAMI")

        self.assertEqual(slots, [1])
        self.assertEqual(notes, [])
        import fourbpp
        got = list(fourbpp.decode_indices(car.portrait_page))
        self.assertTrue(all(i == 10 for i in got[:portrait.PAGE_W * portrait.SHOWN_H]))
        self.assertTrue(all(i == 0 for i in got[portrait.PAGE_W * portrait.SHOWN_H:]))

    def test_a_tga_and_a_bmp_of_a_clut_colour_share_one_page(self):
        from PIL import Image

        import portrait

        clut, _raw = portrait.portrait_clut()
        color = clut[10]

        for name, fmt in (("shot.tga", "TGA"), ("shot.BMP", "BMP"),
                          ("shot.png", "PNG")):
            im = Image.new("RGB", (portrait.PAGE_W, portrait.SHOWN_H), color)
            im.save(os.path.join(self.dir, name), format=fmt)
            bank = bankjson.load(self.write(self.declare(
                car=self.car(portrait=name),
                order=[{"slot": 4, "car": "car1"}])))
            slots, notes = bankjson.install_portraits(bank, self.dir, "CITY")
            self.assertEqual(slots, [4])
            self.assertEqual(notes, [])
            import fourbpp
            got = list(fourbpp.decode_indices(bank.cars["car1"].portrait_page))
            shown = got[:portrait.PAGE_W * portrait.SHOWN_H]
            self.assertTrue(all(i == 10 for i in shown), name)
            self.assertTrue(all(
                i == 0 for i in got[portrait.PAGE_W * portrait.SHOWN_H:]), name)
            self.assertIsNone(bank.cars["car1"].portrait_clut)

    def test_a_short_raster_is_refused(self):
        from PIL import Image

        im = Image.new("RGB", (32, 32), (255, 0, 0))
        im.save(os.path.join(self.dir, "tiny.png"))

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(portrait="tiny.png"))))

        self.assertIn("256", str(caught.exception))
        self.assertNotIn("32768", str(caught.exception))

    def test_a_png_that_is_not_an_image_is_not_read_as_raw(self):
        with open(os.path.join(self.dir, "notes.png"), "wb") as f:
            f.write(b"not a png")

        with self.assertRaises(bankjson.BankError) as caught:
            bankjson.load(self.write(self.declare(
                car=self.car(portrait="notes.png"))))

        self.assertIn("image", str(caught.exception).lower())
        self.assertNotIn("32768", str(caught.exception))

    def test_a_rebake_with_no_portrait_removes_a_stale_raw(self):
        stale = os.path.join(self.dir, "TEST.RAW")
        with open(stale, "wb") as f:
            f.write(b"\x11" * bankjson.PORTRAIT_BYTES)

        bank = bankjson.load(self.write(self.declare()))
        slots, notes = bankjson.install_portraits(bank, self.dir, "TEST")

        self.assertEqual(slots, [])
        self.assertEqual(notes, [])
        self.assertFalse(os.path.isfile(stale))


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
