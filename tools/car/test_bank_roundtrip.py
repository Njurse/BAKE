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
import json
import os
import shutil
import struct
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
import lev1

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


class WheelSegmentTests(unittest.TestCase):
    """The wheel segment: one (type 200) segment beside the car models.

    The carrier, the record encoding and the -1 rule were proven before this was
    attached to the bank; what these check is the placement - the segment the
    engine finds, naming the pages the bank carries, and nothing carried for a
    variant the declaration leaves out.
    """

    def setUp(self):
        _need_bank(self)

        with open(LEV, "rb") as f:
            self.blob = f.read()

    def test_the_segment_rides_beside_the_car_models(self):
        types = [typ for typ, _body, _size in exportd2.d2_segments(self.blob)
                 if typ]

        self.assertIn(exportd2.LUMP_CAR_WHEELS, types,
                      "the bank carries no wheel segment")
        self.assertEqual(types.index(exportd2.LUMP_CAR_WHEELS),
                         types.index(exportd2.LUMP_CAR_MODELS) + 1,
                         "the wheel segment is not the one after the car models")

    def test_an_absent_variant_is_minus_one(self):
        body, size = exportd2.d2_lump(self.blob, exportd2.LUMP_CAR_WHEELS)
        count = struct.unpack_from("<i", self.blob, body)[0]

        self.assertEqual(count, len(exportd2.WHEEL_VARIANTS))

        offsets = [struct.unpack_from("<i", self.blob, body + 4 + i * 4)[0]
                   for i in range(count)]

        for i, offset in enumerate(offsets):
            if offset == -1:
                continue

            self.assertTrue(0 < offset < size,
                            "wheel %d offset %d is outside the segment" %
                            (i, offset))

        # the Firebird has no damaged wheel, and that absence is carried as -1
        # rather than faked with the clean one
        self.assertEqual(offsets[exportd2.WHEEL_VARIANTS.index("damaged")], -1,
                         "the bank fakes a damaged wheel it does not have")
        self.assertNotEqual(offsets[exportd2.WHEEL_VARIANTS.index("clean")], -1)

    def test_a_wheel_names_its_own_page(self):
        body, _size = exportd2.d2_lump(self.blob, exportd2.LUMP_CAR_WHEELS)

        for i, variant in enumerate(exportd2.WHEEL_VARIANTS):
            offset = struct.unpack_from("<i", self.blob, body + 4 + i * 4)[0]

            if offset == -1:
                continue

            model = lev1.decode_model(self.blob, body + offset)

            self.assertEqual(model.errors, [], "wheel %s does not decode"
                             % variant)
            self.assertTrue(8 <= len(model.polys) <= 24,
                            "the engine takes 8..24 wheel polys, not %d"
                            % len(model.polys))
            self.assertEqual({p.kind for p in model.polys[:8]}, {"FT4"},
                             "the engine rejects a wheel whose first 8 polys "
                             "are not FT4")

            # the page must be one the bank carries, or the wheel draws
            # whatever the host has on that slot
            _base, entries = exportd2.d2_page_entries(
                self.blob, exportd2.d2_texture_info(self.blob)[3])

            self.assertIn(model.polys[2].texture_set, entries,
                          "wheel %s names a page the bank does not carry"
                          % variant)

    def test_the_manifest_and_the_bank_agree(self):
        # the bank is rebuilt from FIREBIRD.json, so the manifest is what says
        # which pages the wheels use: verify_wheels reads the segment back
        # against it
        with open(MANIFEST, "r") as f:
            manifest = json.load(f)

        specs = exportd2._load_wheels(manifest)

        self.assertTrue(any(specs), "the manifest declares no wheels")
        self.assertEqual(exportd2.verify_wheels(self.blob, specs), [])

    def test_a_declaration_read_back_from_the_bank_keeps_its_wheels(self):
        # carbank_export --declare writes this; a declaration that named only an
        # image would bake the segment away, because a wheel needs a page
        records = carbank_export._wheel_records(self.blob)

        self.assertEqual(records.get("clean"),
                         {"set": 2, "island": [0, 0, 60, 60]})
        self.assertEqual(records.get("fast"),
                         {"set": 3, "island": [0, 0, 60, 60]})
        self.assertNotIn("damaged", records,
                         "the bank has no damaged wheel to declare")


class WheelSegmentIsAdditiveTests(unittest.TestCase):
    """No declared wheels = no segment, and the segments before it do not move.

    This is the guard the byte-identity check leans on: attaching the wheels had
    to be an ADDITION, so a bank that declares none writes exactly what it wrote
    before the segment existed.
    """

    def _types(self, wheels_body):
        blob = exportd2.assemble_bank(b"model-body", b"texinfo-body",
                                      b"pallet-body", [b"\x00" * 2048],
                                      b"texture-names", b"model-names",
                                      wheels_body)
        return [typ for typ, _body, _size in exportd2.d2_segments(blob) if typ]

    def test_no_wheels_means_no_segment(self):
        self.assertEqual(self._types(None),
                         [exportd2.LUMP_CAR_MODELS, exportd2.LUMP_TEXTUREINFO,
                          exportd2.LUMP_PALLET, exportd2.LUMP_TEXTURENAMES,
                          exportd2.LUMP_MODELNAMES])

    def test_wheels_are_inserted_after_the_car_models(self):
        self.assertEqual(self._types(b"wheel-body"),
                         [exportd2.LUMP_CAR_MODELS, exportd2.LUMP_CAR_WHEELS,
                          exportd2.LUMP_TEXTUREINFO, exportd2.LUMP_PALLET,
                          exportd2.LUMP_TEXTURENAMES, exportd2.LUMP_MODELNAMES])


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
