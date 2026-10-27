#!/usr/bin/env python3
"""Combined-model OBJ reader tests (Blender object/group names)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_core as jc
import jmpped_entries as je
import jmpped_model as jmodel
import jmpped_models as jm

PARTS = {
    "JERI_TORSO", "JERI_HEAD", "JERI_U_ARM_LEFT", "JERI_L_ARM_LEFT",
    "JERI_U_ARM_RIGHT", "JERI_L_ARM_RIGHT", "THIGH_LEFT", "CALF_LEFT",
    "FOOT_LEFT", "THIGH_RIGHT", "CALF_RIGHT", "FOOT_RIGHT", "HAND_LEFT",
    "HAND_RIGHT",
}
MODEL = os.path.join(HERE, "characters", "jerichod3", "model.obj")

SYNTHETIC = """\
# Blender-style combined model
mtllib combined.mtl
o CALF_LEFT.001
v 0.000000 0.000000 0.001000
v 0.001000 0.000000 0.001000
v 0.000000 0.001000 0.001000
vt 0.000000 0.000000
vt 1.000000 0.000000
vt 0.000000 1.000000
vn 0.0000 0.0000 1.0000
g CALF_LEFT_CALF_LEFT
g off
g CALF_LEFT_s2_t12
usemtl s2_t12
f 1/1/1 2/2/1 3/3/1
o JERI_TORSO
v 0.002000 0.000000 0.002000
v 0.003000 0.000000 0.002000
v 0.002000 0.001000 0.002000
vt 0.000000 0.000000
vt 1.000000 0.000000
vt 0.000000 1.000000
g JERI_TORSO_JERI_TORSO
g off
g JERI_TORSO_s23_t9
usemtl s23_t9
f -3/1/1 -2/2/1 -1/3/1
"""


class CombinedReaderTests(unittest.TestCase):
    def _read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.obj")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return jm.read_combined_obj(path, PARTS)

    def test_material_subgroups_resolve_and_off_is_ignored(self):
        groups = self._read(SYNTHETIC)
        self.assertEqual(set(groups), {"CALF_LEFT", "JERI_TORSO"})
        self.assertEqual(len(groups["CALF_LEFT"]["faces"]), 1)
        self.assertEqual(len(groups["JERI_TORSO"]["faces"]), 1)
        self.assertEqual(groups["CALF_LEFT"]["faces"][0][0][2], "s2_t12")
        self.assertEqual(groups["JERI_TORSO"]["faces"][0][0][2], "s23_t9")
        self.assertEqual(len(groups["CALF_LEFT"]["verts"]), 3)
        # Blender Z-up to engine (+Y down), 1/1024 scale.
        x, y, z = groups["CALF_LEFT"]["verts"][0]
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, -1.024)
        self.assertAlmostEqual(z, 0.0)

    def test_negative_indices_resolve(self):
        groups = self._read(SYNTHETIC)
        faces = groups["JERI_TORSO"]["faces"]
        self.assertEqual([corner[0] for corner in faces[0]], [0, 1, 2])

    def test_unknown_group_with_faces_is_refused(self):
        text = SYNTHETIC.replace(
            "g JERI_TORSO_s23_t9\nusemtl s23_t9\n"
            "f -3/1/1 -2/2/1 -1/3/1\n",
            "g UNKNOWN\nusemtl s23_t9\nf -3/1/1 -2/2/1 -1/3/1\n")
        with self.assertRaises(jc.JMPPEDError):
            self._read(text)

    def test_real_character_model_has_all_parts(self):
        if not os.path.isfile(MODEL):
            self.skipTest("combined character model not present")
        groups = jm.read_combined_obj(MODEL, PARTS | {"HIPS"})
        missing = sorted(PARTS - set(groups))
        self.assertFalse(missing, "missing parts: %s" % missing)
        for part in PARTS:
            self.assertTrue(groups[part]["faces"], part)
            self.assertTrue(groups[part]["verts"], part)
        self.assertIn("HIPS", groups)

    def test_write_part_obj_round_trip_and_determinism(self):
        verts = [(0.0, 0.0, 0.0), (1024.0, 0.0, 0.0), (0.0, 1024.0, 0.0)]
        faces = [[
            (0, (0.0, 0.0), "s2_t12"),
            (1, (1.0, 0.0), "s2_t12"),
            (2, (0.0, 1.0), "s2_t12"),
        ]]
        with tempfile.TemporaryDirectory() as tmp:
            blend = os.path.join(tmp, "blender", "CALF_LEFT.obj")
            jm.write_part_obj(blend, "CALF_LEFT", verts, faces, blender=True)
            first = open(blend, encoding="utf-8").read()
            jm.write_part_obj(blend, "CALF_LEFT", verts, faces, blender=True)
            self.assertEqual(open(blend, encoding="utf-8").read(), first)

            rv, rf = jm.read_obj(blend, blender=True)
            self.assertEqual(len(rv), 3)
            self.assertAlmostEqual(rv[0][0], 0.0)
            self.assertAlmostEqual(rv[1][0], 1024.0)
            self.assertAlmostEqual(rv[2][1], 1024.0)
            self.assertEqual(rf[0][0][2], "s2_t12")
            self.assertEqual(rf[0][1][1], (1.0, 0.0))

            engine = os.path.join(tmp, "engine", "CALF_LEFT.obj")
            jm.write_part_obj(engine, "CALF_LEFT", verts, faces, blender=False)
            ev, _ef = jm.read_obj(engine, blender=False)
            self.assertEqual(ev, verts)


class SkeletonBindingTests(unittest.TestCase):
    def test_real_character_bindings_and_offsets(self):
        if not os.path.isfile(os.path.join(HERE, "project.json")):
            self.skipTest("project workspace not present")
        manifest = je.load_manifest(HERE)
        try:
            char = je.character(manifest, "Jericho Driv3r")
        except jc.JMPPEDError:
            self.skipTest("Jericho Driv3r character not present")
        data = jmodel.skeleton_for(HERE, char, manifest)
        self.assertEqual(data["variant"], "jericho")
        self.assertIn("HIPS", data["bindings"].values())
        for part in char["parts"]:
            name = part["part"]
            self.assertIn(name, data["bone_by_model"])
            offset = jmodel.bind_offset(data["bones"], name,
                                        data["bone_by_model"])
            self.assertEqual(len(offset), 3)
            self.assertTrue(all(isinstance(v, float) for v in offset))


if __name__ == "__main__":
    unittest.main()
