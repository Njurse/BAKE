#!/usr/bin/env python3
"""Regression tests for the baked-car normal resolution.

Covers the defect where an FT3/FT4 or flat F3/F4 panel - a Driver 1 record with
no normal indices of its own - was baked as GT with normal index 0, lighting a
whole flank from one unrelated direction (the "black right side" report).

Run from this directory:
    python -m unittest test_bake_normals -v
"""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bake
import lev1

# A single panel in the z = 0 plane. Winding makes u x v = +z the INWARD normal
# (lev1.NORMAL_SIGN), so an outward normal points to -z.
VERTS = [(0, 0, 0), (100, 0, 0), (0, 100, 0)]


def decode(rec):
    model = lev1.decode_model(rec, 0)
    assert not model.errors, model.errors
    assert model.normals, "no point_normals table"
    return model


def faces_outward(model, p):
    ns = model.poly_normals(p)
    tx = sum(x[0] for x in ns)
    ty = sum(x[1] for x in ns)
    tz = sum(x[2] for x in ns)
    g = model._geo_normal(p)
    return tx * g[0] + ty * g[1] + tz * g[2] > 0


class BakeNormals(unittest.TestCase):
    def test_flat_poly_gets_its_own_normal(self):
        """An FT/F poly with n == [] must bake a resolvable, outward normal."""
        polys = [{"kind": "FT3", "set": 1, "id": 0, "v": [0, 1, 2], "n": [],
                  "uv": [(0, 0), (10, 0), (0, 10)], "color": [127, 127, 127]}]
        stats = {}
        rec = bake.build_model_record(VERTS, polys, None, [], 1.0, stats=stats)
        model = decode(rec)
        self.assertEqual(stats["fallback_normals"], 1)
        p = model.polys[0]
        self.assertEqual(len(p.n), len(p.v))
        self.assertTrue(all(i < len(model.normals) for i in p.n))
        self.assertTrue(faces_outward(model, p))

    def test_source_normal_pointing_inward_is_flipped(self):
        """A source entry in the wrong hemisphere must not light from inside."""
        inward = (0, 0, 4096)            # correct table convention
        outward = (0, 0, -4096)          # the measured wrong-way entry
        polys = [{"kind": "GT3", "set": 1, "id": 0, "v": [0, 1, 2],
                  "n": [0, 0, 0], "uv": [(0, 0), (10, 0), (0, 10)],
                  "color": [127, 127, 127]}]
        stats = {}
        rec = bake.build_model_record(VERTS, polys, None, [outward], 1.0, stats=stats)
        model = decode(rec)
        self.assertEqual(stats["flipped_normals"], 1)
        self.assertTrue(faces_outward(model, model.polys[0]))

        # and the already-correct entry is left exactly where it was
        rec2 = bake.build_model_record(VERTS, polys, None, [inward], 1.0)
        model2 = decode(rec2)
        self.assertEqual(list(model2.polys[0].n), [0, 0, 0])
        self.assertTrue(faces_outward(model2, model2.polys[0]))

    def test_flipped_entry_is_detected(self):
        """A baked entry that faces into the panel is visible to the check."""
        polys = [{"kind": "FT3", "set": 1, "id": 0, "v": [0, 1, 2], "n": [],
                  "uv": [(0, 0), (10, 0), (0, 10)], "color": [127, 127, 127]}]
        rec = bake.build_model_record(VERTS, polys, None, [], 1.0)
        model = decode(rec)
        self.assertTrue(faces_outward(model, model.polys[0]))

        # flip the baked table entry in place and rebuild the blob around it
        import struct
        bad = bytearray(rec)
        pnoffs = struct.unpack_from("<i", bad, 28)[0]
        nx, ny, nz, pad = struct.unpack_from("<hhhh", bad, pnoffs)
        struct.pack_into("<hhhh", bad, pnoffs, -nx, -ny, -nz, pad)
        model2 = decode(bytes(bad))
        self.assertFalse(faces_outward(model2, model2.polys[0]))

    def test_pool_shapes_have_no_inward_normals(self):
        """Every cached pool shape must resolve to outward-facing normals."""
        pooldir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "pool")
        paths = sorted(glob.glob(os.path.join(pooldir, "*_geom.json")))
        if not paths:
            self.skipTest("no cached pool - run d1pool.py first")
        checked = 0
        for path in paths:
            with open(path) as f:
                g = json.load(f)
            rec = bake.build_model_record(g["vertices"], g["polys"],
                                          g.get("face_normals"), g.get("point_normals"),
                                          g["scale"])
            model = decode(rec)
            for p in model.polys:
                self.assertEqual(len(p.n), len(p.v), path)
                gvec = model._geo_normal(p)
                if gvec[0] == 0 and gvec[1] == 0 and gvec[2] == 0:
                    continue
                self.assertTrue(faces_outward(model, p), (path, p.kind, p.v, p.n))
                checked += 1
        self.assertGreater(checked, 7000)


if __name__ == "__main__":
    unittest.main()
