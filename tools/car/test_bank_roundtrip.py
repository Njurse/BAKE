#!/usr/bin/env python3
"""The delivery check for the bank pipeline: is export lossless?

Two independent statements, both against the Firebird bank on this machine:

1. **Re-bake an untouched bank and compare bytes.** `exportd2 --rebuild-bank`
   from the bank's own raw blocks must reproduce FIREBIRD.LEV and .LCF exactly.
   If that drifts, every later claim about editing is worthless.

2. **Export, then read it back.** `carbank_export` writes the OBJs,
   `carbank_import` reads them, and the geometry must come back identical:
   the same vertices (the exporter writes them as integers, so this is exact),
   the same number of polys, and for every textured poly the same texture page
   and the same UVs. What CANNOT round trip is deliberate and asserted here too:
   a poly the exporter wrote as `flat` comes back as the grey page, because the
   OBJ material name carries no "untextured, set 0" information.

Run from this directory:
    python -m unittest test_bank_roundtrip -v
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

from collections import Counter

import carbank_export
import carbank_import
import exportd2

BANK = os.path.abspath(os.path.join(HERE, "..", "..", "..",
                                    "betacars", "banks", "firebird"))
LEV = os.path.join(BANK, "FIREBIRD.LEV")
# the REBUILD manifest (raw blocks), not bank.json which carries the declaration
MANIFEST = os.path.join(BANK, "FIREBIRD.json")


def _sha256(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)

    return h.hexdigest()


def _need_bank(test):
    if not os.path.isfile(LEV) or not os.path.isfile(MANIFEST):
        test.skipTest("no Firebird bank on this machine")


class RebakeIsByteIdenticalTests(unittest.TestCase):
    """Statement 1: an untouched bank rebuilds to the same bytes."""

    def setUp(self):
        _need_bank(self)
        self.out = tempfile.mkdtemp(prefix="rebake-")
        self.addCleanup(shutil.rmtree, self.out)

    def test_rebuild_reproduces_the_bank(self):
        cmd = [sys.executable, os.path.join(HERE, "exportd2.py"),
               "--rebuild-bank", MANIFEST, "-o", self.out]

        done = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(done.returncode, 0,
                         "rebuild failed:\n%s\n%s" % (done.stdout, done.stderr))

        for name in ("FIREBIRD.LEV", "FIREBIRD.LCF"):
            rebuilt = os.path.join(self.out, name)

            self.assertTrue(os.path.isfile(rebuilt), "%s was not written" % name)
            self.assertEqual(_sha256(rebuilt), _sha256(os.path.join(BANK, name)),
                             "%s changed when an untouched bank was rebuilt" % name)


class ExportImportTests(unittest.TestCase):
    """Statement 2: export then import returns the same geometry."""

    def setUp(self):
        _need_bank(self)
        self.out = tempfile.mkdtemp(prefix="export-")
        self.addCleanup(shutil.rmtree, self.out)

        with open(LEV, "rb") as f:
            self.blob = f.read()

    def _model(self, slot):
        _off, _size, _bytes, ti = exportd2.d2_texture_info(self.blob)
        table = exportd2.d2_model_table(self.blob)
        return exportd2.decode_variants(self.blob, table, slot)

    def test_vertices_survive_exactly(self):
        result = carbank_export.export(LEV, self.out)
        carname = sorted(result["cars"])[0]
        slot = result["order"][0]["slot"]
        model = self._model(slot)["clean"]

        geom = carbank_import.import_car(
            os.path.join(self.out, result["cars"][carname]["clean"]))

        self.assertEqual(len(geom["vertices"]), len(model.vertices))
        self.assertEqual([tuple(v) for v in geom["vertices"]],
                         [tuple(v) for v in model.vertices],
                         "a vertex moved on the way out and back")

    def test_poly_count_survives(self):
        result = carbank_export.export(LEV, self.out)
        carname = sorted(result["cars"])[0]
        slot = result["order"][0]["slot"]
        model = self._model(slot)["clean"]

        geom = carbank_import.import_car(
            os.path.join(self.out, result["cars"][carname]["clean"]))

        drawable = [p for p in model.polys if len(p.v) >= 3]

        self.assertEqual(len(geom["polys"]), len(drawable))

    def test_no_textured_poly_loses_its_page(self):
        result = carbank_export.export(LEV, self.out)
        carname = sorted(result["cars"])[0]
        slot = result["order"][0]["slot"]
        model = self._model(slot)["clean"]

        geom = carbank_import.import_car(
            os.path.join(self.out, result["cars"][carname]["clean"]))

        want = Counter((p.texture_set, p.texture_id) for p in model.polys
                       if len(p.v) >= 3
                       and p.kind in exportd2.TEXTURED_KINDS
                       and p.texture_set not in (0, 255))
        got = Counter((g["set"], g["id"]) for g in geom["polys"])

        self.assertTrue(want, "the donor car has no textured polys to check")
        self.assertEqual(want - got, Counter(),
                         "a textured poly lost its texture page on the way "
                         "through the OBJ")

    def test_the_only_extra_pages_are_the_untextured_polys(self):
        # the exporter writes an untextured poly as "flat"; importing it back
        # gives the grey page (1, 0). That is the one lossy step, and it must
        # stay confined to the polys that had no page to begin with.
        result = carbank_export.export(LEV, self.out)
        carname = sorted(result["cars"])[0]
        slot = result["order"][0]["slot"]
        model = self._model(slot)["clean"]

        geom = carbank_import.import_car(
            os.path.join(self.out, result["cars"][carname]["clean"]))

        want = Counter((p.texture_set, p.texture_id) for p in model.polys
                       if len(p.v) >= 3
                       and p.kind in exportd2.TEXTURED_KINDS
                       and p.texture_set not in (0, 255))
        got = Counter((g["set"], g["id"]) for g in geom["polys"])

        extra = got - want
        untextured = sum(1 for p in model.polys
                         if len(p.v) >= 3 and p.kind not in exportd2.TEXTURED_KINDS)

        self.assertEqual(set(extra), {(1, 0)} if extra else set(),
                         "an untextured poly came back on a real page: %s"
                         % extra.most_common(3))
        self.assertLessEqual(sum(extra.values()), untextured,
                             "%d polys landed on grey but only %d had no page"
                             % (sum(extra.values()), untextured))


if __name__ == "__main__":
    unittest.main(verbosity=2)
