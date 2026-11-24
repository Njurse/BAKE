#!/usr/bin/env python3
"""handling.policeLights -> LUMP_CAR_LIGHTS (206): validated, laid out the way the
engine's ParseCarImportLights (models.c) reads it, and absent unless a car
declares it."""

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
import exportd2
from test_bankjson import BankFixture

COG = [0, -119, 0]


def _bank(lights_body):
    models = exportd2.build_models_body({0: {"clean": b"A" * 8}})
    return exportd2.assemble_bank(models, b"", b"", [], lights_body=lights_body)


def read_like_the_engine(body, model):
    """The same walk as models.c ParseCarImportLights, for one model: None when
    the engine would ignore the block, else a plain dict."""
    version, num = struct.unpack_from("<ii", body, 0)
    if version != 1 or not 1 <= num <= 13 or len(body) < 8 + 4 * num:
        return None
    off = struct.unpack_from("<i", body, 8 + 4 * model)[0]
    if off == 0:
        return None
    assert off >= 8 + 4 * num and off + 4 <= len(body), "block outside segment"
    mode, style, count, speed = struct.unpack_from("<4B", body, off)
    if mode == 1:
        return {"mode": 1, "style": style}
    assert mode == 2 and 1 <= count <= 8
    assert off + 4 + 12 * count <= len(body), "lamps run past the segment"
    lamps = []
    for i in range(count):
        x, y, z, r, g, b, phase, flags, size = struct.unpack_from(
            "<3h6B", body, off + 4 + 12 * i)
        lamps.append({"pos": [x, y, z], "rgb": [r, g, b], "phase": phase,
                      "mirror": bool(flags & 1), "size": size})
    return {"mode": 2, "speed": speed, "lights": lamps}


class DeclarationTests(BankFixture):
    def load(self, handling):
        car = self.car(handling=handling)
        with open(os.path.join(self.dir, "car1.json"), "w") as f:
            json.dump(car, f)
        decl = self.declare()
        decl["cars"]["car1"] = "car1.json"
        return bankjson.load(self.write(decl))

    def test_style_for_each_city(self):
        want = {"CHICAGO": 0, "HAVANA": 1, "VEGAS": 2, "RIO": 3, "HOST": 255}
        for name, index in want.items():
            got = self.load({"policeLights": {"style": name}}) \
                .cars["car1"].police_lights
            self.assertEqual((got["mode"], got["style"]), (1, index))

    def test_style_is_case_insensitive(self):
        got = self.load({"policeLights": {"style": "havana"}}) \
            .cars["car1"].police_lights
        self.assertEqual(got["style"], 1)

    def test_absent_means_the_engine_default(self):
        self.assertIsNone(self.load({"mass": 4096}).cars["car1"].police_lights)

    def test_at_is_the_models_own_point_not_cog_relative(self):
        # the engine draws the body at cog + vertex and a light at cog + position,
        # so a light's vector is its OBJ coordinate: the COG must not move it
        for cog in ([0, -119, 0], [0, 0, 0], [5, -300, 9]):
            got = self.load({"cog": cog, "policeLights": {"lights": [
                {"at": [-30, -148, -300]}]}}).cars["car1"].police_lights
            self.assertEqual([got["lights"][0][k] for k in "xyz"], [-30, -148, -300])

    def test_pos_is_the_same_vector_as_at(self):
        a = self.load({"policeLights": {"lights": [{"at": [1, -2, 3]}]}})
        p = self.load({"policeLights": {"lights": [{"pos": [1, -2, 3]}]}})
        self.assertEqual(a.cars["car1"].police_lights, p.cars["car1"].police_lights)

    def test_bad_declarations_are_bank_errors(self):
        bad = [
            {"style": "MIAMI"},                              # not a D2 city
            {"style": "RIO", "lights": [{"pos": [0, 0, 0]}]},  # both
            {},                                              # neither
            {"style": "RIO", "speed": 8},                    # speed needs lamps
            {"lights": []},                                  # no lamps
            {"lights": [{"pos": [0, 0, 0]}] * 9},            # more than the engine holds
            {"lights": [{"pos": [0, 0, 0], "at": [0, 0, 0]}]},
            {"lights": [{"pos": [0, 0]}]},
            {"lights": [{"pos": [0, 0, 0], "color": "pink"}]},
            {"lights": [{"pos": [0, 0, 0], "color": [0, 0, 256]}]},
            {"lights": [{"pos": [0, 0, 0], "phase": 256}]},
            {"lights": [{"pos": [0, 0, 0], "mirror": 1}]},
            {"lights": [{"pos": [0, 0, 0], "colour": "red"}]},   # typo'd key
            {"lights": [{"pos": [40000, 0, 0]}]},
        ]
        for spec in bad:
            with self.subTest(spec=spec):
                with self.assertRaises(bankjson.BankError):
                    self.load({"policeLights": spec})


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lights-")
        self.addCleanup(shutil.rmtree, self.dir)

    def lev(self, blob):
        path = os.path.join(self.dir, "T.LEV")
        with open(path, "wb") as f:
            f.write(blob)
        return path

    def cars(self):
        return {
            "bus": {"handling": {"cog": COG, "policeLights": {
                "speed": 20, "lights": [
                    {"at": [-40, -135, -300], "color": "blue"},
                    {"pos": [40, -29, -300], "color": [10, 20, 255],
                     "phase": 128, "mirror": True, "size": 60},
                    {"pos": [0, -29, 250], "color": "amber", "size": 100}]}}},
            "havana": {"handling": {"policeLights": {"style": "HAVANA"}}},
            "plain": {"handling": {"mass": 4096}},
        }

    ORDER = [(0, "plain"), (3, "havana"), (11, "bus")]

    def test_none_when_no_car_declares_it(self):
        self.assertIsNone(exportd2.build_lights_body(
            [(0, "plain")], self.cars()))

    def test_the_engine_reads_back_what_was_declared(self):
        body = exportd2.build_lights_body(self.ORDER, self.cars())
        self.assertEqual(len(body) % 4, 0)

        self.assertIsNone(read_like_the_engine(body, 0))          # says nothing
        self.assertEqual(read_like_the_engine(body, 3), {"mode": 1, "style": 1})

        bus = read_like_the_engine(body, 11)
        self.assertEqual(bus["mode"], 2)
        self.assertEqual(bus["speed"], 20)
        self.assertEqual([l["pos"] for l in bus["lights"]],
                         [[-40, -135, -300], [40, -29, -300], [0, -29, 250]])
        self.assertEqual(bus["lights"][0]["rgb"], [90, 90, 255])
        self.assertEqual(bus["lights"][1]["rgb"], [10, 20, 255])
        self.assertEqual([l["phase"] for l in bus["lights"]], [0, 128, 0])
        self.assertEqual([l["mirror"] for l in bus["lights"]], [False, True, False])
        # 100 % is stored as 0, the engine's "stock size"
        self.assertEqual([l["size"] for l in bus["lights"]], [0, 60, 0])

    def test_default_speed_is_sixteen(self):
        cars = {"a": {"handling": {"policeLights": {
            "lights": [{"pos": [1, 2, 3]}]}}}}
        body = exportd2.build_lights_body([(2, "a")], cars)
        self.assertEqual(read_like_the_engine(body, 2)["speed"], 16)

    def test_host_style(self):
        cars = {"a": {"handling": {"policeLights": {"style": "HOST"}}}}
        body = exportd2.build_lights_body([(5, "a")], cars)
        self.assertEqual(read_like_the_engine(body, 5),
                         {"mode": 1, "style": 255})

    def test_segment_lands_in_the_bank(self):
        body = exportd2.build_lights_body(self.ORDER, self.cars())
        blob = _bank(body)
        got = exportd2.d2_lump(blob, exportd2.LUMP_CAR_LIGHTS)
        self.assertIsNotNone(got)
        start, size = got
        self.assertEqual(blob[start:start + size], body)
        self.assertEqual(exportd2.verify_lights(self.lev(blob), body), [])

    def test_a_bank_without_it_is_byte_identical(self):
        self.assertEqual(_bank(None), _bank(None))
        self.assertIsNone(exportd2.d2_lump(_bank(None), exportd2.LUMP_CAR_LIGHTS))

    def test_verify_catches_a_missing_or_stray_segment(self):
        body = exportd2.build_lights_body(self.ORDER, self.cars())
        self.assertTrue(exportd2.verify_lights(self.lev(_bank(None)), body))
        self.assertTrue(exportd2.verify_lights(self.lev(_bank(body)), None))

        other = exportd2.build_lights_body(
            [(3, "havana")], self.cars())
        self.assertTrue(exportd2.verify_lights(self.lev(_bank(other)), body))

    def test_lump_id_is_free(self):
        taken = {exportd2.LUMP_CAR_WHEELS, exportd2.LUMP_CAR_SOUNDS,
                 exportd2.LUMP_CAR_SIRENS, exportd2.LUMP_POLICE_VOICES,
                 exportd2.LUMP_CAR_WHEELS2, exportd2.LUMP_CAR_DRIVE}
        self.assertNotIn(exportd2.LUMP_CAR_LIGHTS, taken)

    def test_bad_manifest_value_is_a_bank_error(self):
        with self.assertRaises(bankjson.BankError):
            exportd2.build_lights_body(
                [(0, "a")], {"a": {"handling": {"policeLights": {"style": "X"}}}})


if __name__ == "__main__":
    unittest.main()
