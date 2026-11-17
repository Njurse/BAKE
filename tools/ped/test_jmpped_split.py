#!/usr/bin/env python3
"""Splitter tests on an isolated copy of the Jericho Driv3r character."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_core as jc
import jmpped_entries as je
import jmpped_model as jmodel
import jmpped_models as jm

SRC = os.path.join(HERE, "characters", "jerichod3")
SKEL = os.path.join(HERE, "out", "source", "havana", "skeleton",
                    "skeleton.json")


class SplitTests(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(SRC) or not os.path.isfile(SKEL):
            self.skipTest("the Jericho Driv3r workspace is not present")
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = self.tmp.name
        char_dir = os.path.join(root, "characters", "test")
        shutil.copytree(SRC, char_dir)
        char = jc.load_json(os.path.join(char_dir, "character.json"))
        je.rewrite_paths(char, "characters/jerichod3", "characters/test")
        char["name"] = "Test Character"
        char["folder"] = "characters/test"
        skel_dir = os.path.join(root, "out", "source", "havana", "skeleton")
        os.makedirs(skel_dir)
        shutil.copy2(SKEL, os.path.join(skel_dir, "skeleton.json"))
        project = {
            "project_version": 2,
            "format_version": jc.VERSION_V5,
            "source": {
                "skeleton": {
                    "json": "out/source/havana/skeleton/skeleton.json",
                }
            },
            "characters": [char],
            "source_models": {"jericho": {"texture_page_sets": [23, 25, 2]}},
            "pages": [],
            "build": {"parts": []},
        }
        jc.save_json(os.path.join(root, "project.json"), project)
        jc.save_json(os.path.join(char_dir, "character.json"), char)

        # Move one vertex so the split has a real change to report even when the
        # copied per-part caches already match the combined model.
        model_path = je.abs_path(root, char["model"]["path"])
        with open(model_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("v "):
                fields = line.split()
                fields[1] = "%.6f" % (float(fields[1]) + 0.001)
                lines[i] = " ".join(fields)
                break
        with open(model_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _manifest(self):
        return je.load_manifest(self.root)

    def _char(self):
        return je.character(self._manifest(), "Test Character")

    def test_split_writes_parts_and_subtracts_bind_offset(self):
        changed = jmodel.split_character(self.root, self._char(), force=True)
        self.assertGreater(changed, 0)
        char = self._char()
        self.assertTrue(char["model"].get("split"))
        self.assertEqual(
            {int(k): int(v) for k, v in
             (char.get("texture_page_map") or {}).items()},
            {23: 26, 25: 27, 2: 3})
        self.assertEqual(char["model"]["sha256"],
                         jc.sha256_file(je.abs_path(self.root,
                                                    char["model"]["path"])))
        for part in char["parts"]:
            self.assertTrue(os.path.isfile(je.abs_path(self.root,
                                                       part["obj_edit"])))
            self.assertTrue(os.path.isfile(je.abs_path(self.root,
                                                       part["obj_raw"])))
            self.assertEqual(part["obj_raw_sha256"],
                             jc.sha256_file(je.abs_path(self.root,
                                                        part["obj_raw"])))
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "characters", "test", "models", "reference",
            "HIPS.obj")))

        data = jmodel.skeleton_for(self.root, char, self._manifest())
        # A textured character's editable model is the material export; its
        # page-space model is the one whose groups are the part names.
        model_rel = char["model"].get("page_path") or char["model"]["path"]
        groups = jm.read_combined_obj(
            je.abs_path(self.root, model_rel),
            set(jmodel.part_names(char)) | set(data["bindings"].values()))
        offset = jmodel.bind_offset(data["bones"], "CALF_LEFT",
                                    data["bone_by_model"])
        part = next(p for p in char["parts"] if p["part"] == "CALF_LEFT")
        ev, _faces = jm.read_obj(je.abs_path(self.root, part["obj_raw"]),
                                 blender=False)
        src = groups["CALF_LEFT"]["verts"][0]
        for i in range(3):
            self.assertAlmostEqual(ev[0][i], src[i] - offset[i], places=3)

    def test_second_split_is_idempotent(self):
        jmodel.split_character(self.root, self._char(), force=True)
        before = {p["part"]: jc.sha256_file(je.abs_path(self.root, p["obj_edit"]))
                  for p in self._char()["parts"]}
        changed = jmodel.split_character(self.root, self._char(), force=False)
        self.assertEqual(changed, 0)
        after = {p["part"]: jc.sha256_file(je.abs_path(self.root, p["obj_edit"]))
                 for p in self._char()["parts"]}
        self.assertEqual(before, after)

    def test_changed_part_is_detected(self):
        jmodel.split_character(self.root, self._char(), force=True)
        char = self._char()
        model = je.abs_path(self.root, char["model"]["path"])
        lines = open(model, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("v "):
                fields = line.split()
                fields[1] = "%.6f" % (float(fields[1]) + 0.001)
                lines[i] = " ".join(fields)
                break
        else:
            self.fail("the combined model has no vertex")
        with open(model, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

        self.assertTrue(jmodel.model_pending(self.root, char))
        changed = jmodel.split_character(self.root, self._char(), force=False)
        self.assertGreater(changed, 0)
        char = self._char()
        self.assertTrue(any(
            p["obj_edit_sha256"] != jc.sha256_file(je.abs_path(self.root,
                                                               p["obj_edit"]))
            for p in char["parts"]))

    def test_split_pending_uses_hash_and_force(self):
        self.assertGreater(jmodel.split_pending(self.root), 0)
        self.assertEqual(jmodel.split_pending(self.root), 0)
        with mock.patch.object(jmodel, "model_pending", return_value=False), \
                mock.patch.object(jmodel, "split_character",
                                  return_value=0) as split:
            self.assertEqual(jmodel.split_pending(self.root, force=True), 0)
            split.assert_called_once()
            split.reset_mock()
            self.assertEqual(jmodel.split_pending(self.root, force=False), 0)
            split.assert_not_called()


class SourceNamingTests(unittest.TestCase):
    """A Tanner exported under Tanner's names must not come back JERI_*.

    tannerd3 fills the six torso/head/arm slots with Tanner's geometry, and a
    Blender export names those objects TORSO, HEAD, ... The splitter follows
    the export's names (and the tanner binding variant) instead of keeping the
    names of the Jericho clone it started from.
    """

    UPPER = {
        "JERI_TORSO": "TORSO",
        "JERI_HEAD": "HEAD",
        "JERI_U_ARM_LEFT": "U_ARM_LEFT",
        "JERI_L_ARM_LEFT": "L_ARM_LEFT",
        "JERI_U_ARM_RIGHT": "U_ARM_RIGHT",
        "JERI_L_ARM_RIGHT": "L_ARM_RIGHT",
    }

    def setUp(self):
        src = os.path.join(HERE, "characters", "jericho")
        if not os.path.isdir(src) or not os.path.isfile(SKEL):
            self.skipTest("the Jericho workspace is not present")
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = self.tmp.name
        char_dir = os.path.join(root, "characters", "test")
        shutil.copytree(src, char_dir)
        char = jc.load_json(os.path.join(char_dir, "character.json"))
        je.rewrite_paths(char, "characters/jericho", "characters/test")
        char["name"] = "Tanner Test"
        char["folder"] = "characters/test"
        skel_dir = os.path.join(root, "out", "source", "havana", "skeleton")
        os.makedirs(skel_dir)
        shutil.copy2(SKEL, os.path.join(skel_dir, "skeleton.json"))
        project = {
            "project_version": 2,
            "format_version": jc.VERSION_V5,
            "source": {
                "skeleton": {
                    "json": "out/source/havana/skeleton/skeleton.json",
                }
            },
            "characters": [char],
            "source_models": {"jericho": {"texture_page_sets": [23, 25, 2]}},
            "pages": [],
            "build": {"parts": []},
        }
        jc.save_json(os.path.join(root, "project.json"), project)
        jc.save_json(os.path.join(char_dir, "character.json"), char)

        # Rename the combined model's objects the way the Tanner export names
        # them (Blender writes OBJECT_material as a group name).
        model = je.abs_path(root, char["model"]["path"])
        with open(model, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("o "):
                new = self.UPPER.get(line[2:].strip())
                if new:
                    lines[i] = "o %s_s2_t2" % new
        with open(model, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_blender_prefix_on_a_limb_is_stripped(self):
        # The base set's prefix leaks onto limbs in some Blender scenes; the
        # archive never names a limb JERI_*, so strip it as a last resort.
        self.assertEqual(
            jm._resolve_part_group("JERI_CALF_LEFT_CALF_LEFT__s3_t12",
                                   {"CALF_LEFT"}),
            "CALF_LEFT")
        self.assertEqual(
            jm._resolve_part_group("JERI_CALF_LEFT_CALF_LEFT__s3_t12",
                                   {"JERI_HEAD", "CALF_LEFT"}),
            "CALF_LEFT")

    def test_source_names_are_adopted(self):
        char = je.character(je.load_manifest(self.root), "Tanner Test")
        jmodel.split_character(self.root, char, force=True)
        char = je.character(je.load_manifest(self.root), "Tanner Test")
        self.assertEqual(char["skeleton"], "tanner")
        for slot, new in enumerate(["TORSO", "HEAD", "U_ARM_LEFT",
                                    "L_ARM_LEFT", "U_ARM_RIGHT",
                                    "L_ARM_RIGHT"]):
            part = char["parts"][slot]
            self.assertEqual(part["part"], new)
            self.assertTrue(os.path.isfile(je.abs_path(self.root,
                                                       part["obj_edit"])))
            self.assertTrue(os.path.isfile(je.abs_path(self.root,
                                                       part["obj_raw"])))
        for folder in (("models", "blender"), ("models", "engine"),
                       ("textures", "crops")):
            names = os.listdir(os.path.join(self.root, "characters", "test",
                                            *folder))
            self.assertFalse([n for n in names if n.startswith("JERI_")],
                             "%s still carries JERI_ names: %s"
                             % ("/".join(folder), names))


class PartSideTests(unittest.TestCase):
    """Every character must keep each part on the bone of its own side.

    A cross-side binding is what stored the shoes 56 engine units (two toe
    widths) away from the joint that rotates them. `jmpped_model.bind_offset`
    refuses such a pairing, so walking every character in the real project is
    enough to keep the crossover from coming back.
    """

    def setUp(self):
        if not os.path.isfile(os.path.join(HERE, "project.json")):
            self.skipTest("the authoring workspace is not present")
        if not os.path.isfile(SKEL):
            self.skipTest("the source skeleton export is not present")
        self.manifest = je.load_manifest(HERE)

    def test_every_character_part_binds_to_its_own_side(self):
        characters = self.manifest.get("characters") or []
        self.assertTrue(characters, "project.json lists no characters")
        checked = 0
        for character in characters:
            data = jmodel.skeleton_for(HERE, character, self.manifest)
            for part in jmodel.part_names(character):
                offset = jmodel.bind_offset(data["bones"], part,
                                            data["bone_by_model"])
                checked += 1
                if part.endswith("_LEFT"):
                    self.assertGreater(offset[0], 0,
                                       "%s/%s sits on the right of the skeleton"
                                       % (character.get("name"), part))
                elif part.endswith("_RIGHT"):
                    self.assertLess(offset[0], 0,
                                    "%s/%s sits on the left of the skeleton"
                                    % (character.get("name"), part))
        self.assertGreater(checked, 0, "no part was checked")

    def test_the_toe_binding_is_not_crossed(self):
        import jmpped_level as jl
        for name in ("TANNER_BINDINGS", "JERICHO_BINDINGS"):
            bindings = getattr(jl, name)
            self.assertEqual(bindings.get("LTOE"), "FOOT_LEFT", name)
            self.assertEqual(bindings.get("RTOE"), "FOOT_RIGHT", name)
        exported = jc.load_json(SKEL)
        for variant, data in (exported.get("variants") or {}).items():
            bindings = data.get("bindings") or {}
            self.assertEqual(bindings.get("LTOE"), "FOOT_LEFT",
                             "%s: skeleton export still crosses the toes"
                             % variant)
            self.assertEqual(bindings.get("RTOE"), "FOOT_RIGHT",
                             "%s: skeleton export still crosses the toes"
                             % variant)


if __name__ == "__main__":
    unittest.main()
