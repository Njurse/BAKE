#!/usr/bin/env python3
"""Tests for preview_bank.py - declaration paths must resolve once.

The preview split left one root doing two jobs: a car's own handling.json paths
are relative to the car directory, but the bank's OTHER cars are relative to the
bank.json directory. Passing the car root to resolve_wheels doubled the car path
("cars/A/cars/A/handling.json") and crashed the preview of a slot that borrows
its wheels. These pin both roots.

Run from this directory:
    python -m unittest test_preview_bank -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import preview_bank


class ResolveWheelsTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, rel, obj):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

        return path

    def _bank(self, cars):
        self._write("bank.json", {"declare": {"cars": cars}})
        return preview_bank.load_bank(os.path.join(self.root, "bank.json"))

    def test_a_car_with_wheels_keeps_its_own_directory(self):
        self._write("cars/A/handling.json",
                    {"clean": "a.obj", "wheels": {"clean": {"png": "w.png"}}})
        root, bank = self._bank({"A": "cars/A/handling.json"})
        _key, car_root, car = preview_bank.resolve_car(root, bank, "A")

        wheel_root, wheel_car = preview_bank.resolve_wheels(
            root, bank, car, car_root)

        self.assertEqual(wheel_root, car_root)
        self.assertIs(wheel_car, car)

    def test_borrowed_wheels_resolve_from_the_bank_root(self):
        self._write("cars/A/handling.json", {"clean": "a.obj"})
        self._write("cars/B/handling.json",
                    {"clean": "b.obj",
                     "wheels": {"clean": {"png": "w.png", "set": 50,
                                          "island": [0, 0, 64, 64]}}})
        root, bank = self._bank({"A": "cars/A/handling.json",
                                 "B": "cars/B/handling.json"})
        _key, car_root, car = preview_bank.resolve_car(root, bank, "A")

        wheel_root, wheel_car = preview_bank.resolve_wheels(
            root, bank, car, car_root)

        self.assertEqual(wheel_root, os.path.join(root, "cars", "B"),
                         "another car's paths are relative to bank.json")
        self.assertEqual(wheel_car["wheels"]["clean"]["png"], "w.png")
        self.assertEqual(preview_bank.clean_model(wheel_root, wheel_car),
                         os.path.join(wheel_root, "b.obj"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
