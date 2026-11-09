#!/usr/bin/env python3
"""Public command names forward to the existing scripts.

    python -m unittest test_public_commands -v
"""
import importlib.util
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PED = os.path.join(HERE, "..", "ped")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DispatchTests(unittest.TestCase):
    def test_pool_bake_picks_the_existing_step(self):
        mod = _load(os.path.join(HERE, "pool-bake.py"), "pool_bake")
        self.assertEqual(mod.which(["--levels", "LEVELS", "--render"]), "d1pool")
        self.assertEqual(mod.which(["--pool", "out/pool.json", "--first", "13"]), "bake")
        self.assertEqual(mod.which(["--city", "FRISCO"]), "bake")

    def test_d2_export_picks_export_or_one_car(self):
        mod = _load(os.path.join(HERE, "d2-export.py"), "d2_export")
        self.assertEqual(mod.which(["--selftest"]), "exportd2")
        self.assertEqual(mod.which(["--rebuild-bank", "bank.json"]), "exportd2")
        self.assertEqual(mod.which(["CITY.LEV", "-o", "out"]), "exportd2")
        self.assertEqual(mod.which(["--model", "1", "--name", "FIREBIRD"]), "make_new_bank")
        self.assertEqual(mod.which(["--lev", "HAVANA.LEV", "--slot", "2"]), "make_new_bank")


class RunTests(unittest.TestCase):
    def _run(self, args):
        return subprocess.run(
            [sys.executable] + args, cwd=HERE,
            capture_output=True, text=True)

    def test_d2_export_and_exportd2_selftest(self):
        new = self._run(["d2-export.py", "--selftest"])
        old = self._run(["exportd2.py", "--selftest"])
        self.assertEqual(new.returncode, 0, new.stderr)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertIn("clean", new.stdout)
        self.assertIn("clean", old.stdout)

    def test_help_reaches_each_implementation(self):
        pool = self._run(["pool-bake.py", "--help"])
        self.assertEqual(pool.returncode, 0, pool.stderr)
        self.assertIn("d1pool.py", pool.stdout)
        self.assertIn("bake.py", pool.stdout)

        extract = self._run(["pool-bake.py", "--levels", "x", "--help"])
        self.assertEqual(extract.returncode, 0, extract.stderr)
        self.assertIn("d1pool", extract.stdout.lower())

        baked = self._run(["pool-bake.py", "--pool", "x", "--help"])
        self.assertEqual(baked.returncode, 0, baked.stderr)
        self.assertIn("pool.json", baked.stdout)

        one = self._run(["d2-export.py", "--model", "1", "--help"])
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertIn("one-car", one.stdout)

        bank = self._run(["bank-bake.py", "--help"])
        old = self._run(["carbank_bake.py", "--help"])
        self.assertEqual(bank.returncode, 0, bank.stderr)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertIn("declare.scale", bank.stdout)
        self.assertIn("declare.scale", old.stdout)

        ped = subprocess.run(
            [sys.executable, "ped-bake.py", "--help"], cwd=PED,
            capture_output=True, text=True)
        old_ped = subprocess.run(
            [sys.executable, "jmpped.py", "--help"], cwd=PED,
            capture_output=True, text=True)
        self.assertEqual(ped.returncode, 0, ped.stderr)
        self.assertEqual(old_ped.returncode, 0, old_ped.stderr)
        self.assertIn("bake", ped.stdout)
        self.assertIn("bake", old_ped.stdout)


if __name__ == "__main__":
    unittest.main()
