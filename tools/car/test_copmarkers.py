#!/usr/bin/env python3
"""COPLIGHT_* marker objects: dropped from the car's geometry, read back as lamps
(copmarkers.py), written into handling.json, and baked into LUMP_CAR_LIGHTS."""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import copmarkers
import exportd2
import obj2geom
from test_police_lights import read_like_the_engine

# a one-triangle "car" with an unrelated group, then two markers and a loose point
OBJ = """# test
g body
v 0 0 0
v 100 0 0
v 0 -50 0
f 1 2 3
g COPLIGHT_L_blue_COPLIGHT_L_blue.001
v -10 -100 -200
v -14 -96 -204
f 4 5 5
g COPLIGHT_R_blue_p128
v 10 -100 -200
g body2
v 0 0 100
v 100 0 100
v 0 -50 100
f 7 8 9
"""


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="markers-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.obj = os.path.join(self.dir, "car.obj")
        with open(self.obj, "w") as f:
            f.write(OBJ)

    def test_markers_never_reach_the_geometry(self):
        verts, uvs, faces = obj2geom.parse_obj(self.obj)
        self.assertEqual(len(verts), 6)                  # the two body triangles
        self.assertEqual(len(faces), 2)
        self.assertTrue(all(0 <= i < len(verts) for vi, _ in faces for i in vi))
        # the second triangle was renumbered past the dropped marker points
        self.assertEqual(faces[1][0], [3, 4, 5])
        self.assertEqual(verts[3], (0.0, 0.0, 100.0))

    def test_an_obj_without_markers_is_untouched(self):
        plain = os.path.join(self.dir, "plain.obj")
        with open(plain, "w") as f:
            f.write("g a\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.assertEqual(obj2geom.parse_obj(plain),
                         ([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                          [], [([0, 1, 2], [-1, -1, -1])]))

    def test_marker_point_is_the_middle_of_its_box(self):
        got = dict(obj2geom.read_markers(self.obj))
        self.assertEqual(got["COPLIGHT_L_blue_COPLIGHT_L_blue.001"], (-12.0, -98.0, -202.0))
        self.assertEqual(got["COPLIGHT_R_blue_p128"], (10.0, -100.0, -200.0))   # one vertex

    def test_names_become_lamps(self):
        lamp = copmarkers.lamp_from_marker(
            "COPLIGHT_Rear_Cube_amber_m_s60_p64", (1.4, -2.6, 3), 0)
        self.assertEqual(lamp, {"name": "Rear", "at": [1, -3, 3], "color": "amber",
                                "phase": 64, "mirror": True, "size": 60})

    def test_blender_object_mesh_doubling_and_suffix(self):
        lamp = copmarkers.lamp_from_marker(
            "COPLIGHT_L_blue_COPLIGHT_L_blue.001", (0, 0, 0), 0)
        self.assertEqual((lamp["name"], lamp["color"]), ("L", "blue"))

    def test_unnamed_lamp_gets_a_number(self):
        self.assertEqual(copmarkers.lamp_from_marker("COPLIGHT", (0, 0, 0), 2)["name"],
                         "light3")

    def test_the_bakes_scale_and_flips_are_applied(self):
        self.assertEqual(copmarkers.to_engine((10, 20, 30), 1.0), (10, 20, 30))
        self.assertEqual(copmarkers.to_engine((10, 20, 30), 0.5, flip_y=True),
                         (5, -10, 15))
        self.assertEqual(copmarkers.to_engine((10, 20, 30), 2, True, True),
                         (20, -40, -60))

    def test_scale_comes_from_the_bank(self):
        car = os.path.join(self.dir, "cars", "c", "handling.json")
        os.makedirs(os.path.dirname(car))
        with open(os.path.join(self.dir, "bank.json"), "w") as f:
            json.dump({"declare": {"scale": 1}}, f)
        self.assertEqual(copmarkers.find_bank_scale(car)[0], 1.0)
        os.remove(os.path.join(self.dir, "bank.json"))
        self.assertEqual(copmarkers.find_bank_scale(car)[0], copmarkers.D1_OBJ_SCALE)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="markers-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.car = os.path.join(self.dir, "cars", "bus")
        os.makedirs(self.car)
        with open(os.path.join(self.car, "bus.obj"), "w") as f:
            f.write(OBJ)
        with open(os.path.join(self.dir, "bank.json"), "w") as f:
            json.dump({"declare": {"scale": 1}}, f)

    def write(self, handling, eol="\n"):
        path = os.path.join(self.car, "handling.json")
        text = json.dumps({"model": 11, "clean": "bus.obj", "handling": handling},
                          indent=1).replace("\n", eol)
        with open(path, "w", newline="") as f:
            f.write(text)
        return path

    def test_write_then_bake_reads_back_the_same_lamps(self):
        path = self.write({"cog": [0, -119, 0], "mass": 4096})
        self.assertEqual(copmarkers.main([path, "--write"]), 0)

        data = json.load(open(path))
        lights = data["handling"]["policeLights"]["lights"]
        self.assertEqual([l["name"] for l in lights], ["L", "R"])
        self.assertEqual(data["handling"]["mass"], 4096)          # nothing else moved

        body = exportd2.build_lights_body([(11, "bus")], {"bus": data})
        bus = read_like_the_engine(body, 11)
        # the marker's own OBJ point; the COG (-119 up) must not move it
        self.assertEqual([l["pos"] for l in bus["lights"]],
                         [[-12, -98, -202], [10, -100, -200]])
        self.assertEqual([l["phase"] for l in bus["lights"]], [0, 128])

    def test_look_only_writes_nothing(self):
        path = self.write({"cog": [0, 0, 0]})
        before = open(path).read()
        self.assertEqual(copmarkers.main([path]), 0)
        self.assertEqual(open(path).read(), before)

    def test_a_city_style_is_not_thrown_away_by_accident(self):
        path = self.write({"cog": [0, 0, 0], "policeLights": {"style": "HAVANA"}})
        with self.assertRaises(SystemExit):
            copmarkers.main([path, "--write"])
        self.assertEqual(json.load(open(path))["handling"]["policeLights"],
                         {"style": "HAVANA"})
        self.assertEqual(copmarkers.main([path, "--write", "--replace-style"]), 0)
        self.assertIn("lights", json.load(open(path))["handling"]["policeLights"])

    def test_speed_survives_a_rewrite(self):
        path = self.write({"cog": [0, 0, 0], "policeLights": {
            "speed": 30, "lights": [{"pos": [0, 0, 0]}]}})
        copmarkers.main([path, "--write"])
        spec = json.load(open(path))["handling"]["policeLights"]
        self.assertEqual(spec["speed"], 30)
        self.assertEqual(len(spec["lights"]), 2)

    def test_the_files_line_endings_are_kept(self):
        path = self.write({"cog": [0, 0, 0]}, eol="\r\n")
        copmarkers.main([path, "--write"])
        raw = open(path, "rb").read()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_no_markers_is_reported_not_written(self):
        path = self.write({"cog": [0, 0, 0]})
        with open(os.path.join(self.car, "bus.obj"), "w") as f:
            f.write("g a\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.assertEqual(copmarkers.main([path, "--write"]), 1)
        self.assertNotIn("policeLights", json.load(open(path))["handling"])


if __name__ == "__main__":
    unittest.main()
