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
    def test_flat_poly_keeps_its_kind_and_has_no_normals(self):
        """An FT/F poly must come back as FT/F, with no normal indices at all.

        The old bake re-encoded every poly as GT so it could carry a normal;
        that is what made an untextured F3 sample garbage UVs and an FT3 get
        smoothed shading. The kind is now preserved and the record simply has
        no normal field, so there is nothing to resolve.
        """
        polys = [{"kind": "FT3", "set": 1, "id": 0, "v": [0, 1, 2], "n": [],
                  "uv": [(0, 0), (10, 0), (0, 10)], "color": [127, 127, 127]}]
        rec = bake.build_model_record(VERTS, polys, None, [], 1.0)
        model = decode(rec)
        p = model.polys[0]
        self.assertEqual(p.kind, "FT3")
        self.assertEqual(tuple(p.n), ())
        self.assertEqual(p.uv, ((0, 0), (10, 0), (0, 10)))
        self.assertTrue(faces_outward(model, p))

    def test_untextured_flat_poly_stays_f3(self):
        """An F3 has no UVs and no normals: it must not acquire either."""
        polys = [{"kind": "F3", "set": 0, "id": 0, "v": [0, 1, 2], "n": [],
                  "uv": [], "color": [40, 60, 90]}]
        rec = bake.build_model_record(VERTS, polys, None, [], 1.0)
        model = decode(rec)
        p = model.polys[0]
        self.assertEqual(p.kind, "F3")
        self.assertEqual(tuple(p.n), ())
        self.assertEqual(tuple(p.uv), ())
        self.assertEqual(tuple(p.color), (40, 60, 90))

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
        """A baked GT entry that faces into the panel is visible to the check."""
        polys = [{"kind": "GT3", "set": 1, "id": 0, "v": [0, 1, 2], "n": [],
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
