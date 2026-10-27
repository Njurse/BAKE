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
        groups = jm.read_combined_obj(
            je.abs_path(self.root, char["model"]["path"]),
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


if __name__ == "__main__":
    unittest.main()
