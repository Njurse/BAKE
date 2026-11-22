#!/usr/bin/env python3
"""handling.driveWheels -> LUMP_CAR_DRIVE (205): validated, one byte per model
slot, and absent unless a car declares it."""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import bankjson
import exportd2
from test_bankjson import BankFixture


def _bank(drive_body):
    models = exportd2.build_models_body({0: {"clean": b"A" * 8}})
    return exportd2.assemble_bank(models, b"", b"", [], drive_body=drive_body)


class DeclarationTests(BankFixture):
    def load(self, handling):
        # handling lives in the car's own file (cars/<car>/handling.json)
        car = self.car(handling=handling)
        with open(os.path.join(self.dir, "car1.json"), "w") as f:
            json.dump(car, f)
        decl = self.declare()
        decl["cars"]["car1"] = "car1.json"
        return bankjson.load(self.write(decl))

    def test_each_value_loads(self):
        for value in ("front", "rear", "all"):
            self.assertEqual(self.load({"driveWheels": value})
                             .cars["car1"].drive_wheels, value)

    def test_absent_means_no_override(self):
        self.assertIsNone(self.load({"mass": 4096}).cars["car1"].drive_wheels)

    def test_unknown_value_is_a_bank_error(self):
        for value in ("fwd", "Front", 1, None):
            with self.assertRaises(bankjson.BankError):
                self.load({"driveWheels": value})


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="drive-")
        self.addCleanup(shutil.rmtree, self.dir)

    def lev(self, blob):
        path = os.path.join(self.dir, "T.LEV")
        with open(path, "wb") as f:
            f.write(blob)
        return path

    def test_no_declaration_writes_no_segment(self):
        cars = {"a": {"handling": {"mass": 4096}}}
        self.assertIsNone(exportd2.build_drive_body([(2, "a")], cars))
        self.assertEqual(_bank(None), exportd2.assemble_bank(
            exportd2.build_models_body({0: {"clean": b"A" * 8}}), b"", b"", []))

    def test_bytes_land_on_their_model_slot(self):
        cars = {"f": {"handling": {"driveWheels": "front"}},
                "r": {"handling": {"driveWheels": "rear"}},
                "x": {"handling": {"driveWheels": "all"}},
                "n": {"handling": {}}}
        body = exportd2.build_drive_body(
            [(0, "n"), (4, "f"), (9, "r"), (12, "x")], cars)
        want = bytearray(16)
        want[4], want[9], want[12] = 1, 2, 3
        self.assertEqual(body, bytes(want))

        blob = _bank(body)
        got = exportd2.d2_lump(blob, exportd2.LUMP_CAR_DRIVE)
        self.assertIsNotNone(got)
        start, size = got
        self.assertEqual(size, 16)
        self.assertEqual(blob[start:start + size], bytes(want))
        self.assertEqual(exportd2.verify_drive(self.lev(blob), body), [])

    def test_verify_catches_a_missing_or_stray_segment(self):
        body = bytes([0, 1] + [0] * 14)
        self.assertTrue(exportd2.verify_drive(self.lev(_bank(None)), body))
        self.assertTrue(exportd2.verify_drive(self.lev(_bank(body)), None))
        other = bytes([0, 3] + [0] * 14)
        self.assertTrue(exportd2.verify_drive(self.lev(_bank(other)), body))

    def test_bad_manifest_value_is_a_bank_error(self):
        with self.assertRaises(bankjson.BankError):
            exportd2.build_drive_body(
                [(0, "a")], {"a": {"handling": {"driveWheels": "4wd"}}})


if __name__ == "__main__":
    unittest.main()
