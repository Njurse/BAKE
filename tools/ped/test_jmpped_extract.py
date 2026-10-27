#!/usr/bin/env python3
"""Extraction guard and source-skeleton tests."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_core as jc
import jmpped_extract as jx


class InstallGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmpped-extract-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.src = os.path.join(self.root, "stage", "characters", "source")
        self.dst = os.path.join(self.root, "characters", "source")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "payload.txt"), "w",
                  encoding="utf-8") as f:
            f.write("new")

    def _character(self, name):
        os.makedirs(self.dst, exist_ok=True)
        jc.save_json(os.path.join(self.dst, "character.json"),
                     {"name": name})

    def test_refuses_different_character(self):
        self._character("Custom")
        with self.assertRaises(jc.JMPPEDError):
            jx._install_dir(self.src, self.dst, protect_name="Source")
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "payload.txt")) is False)
        self.assertTrue(os.path.isfile(os.path.join(self.dst,
                                                    "character.json")))

    def test_allows_matching_source_character(self):
        self._character("Source")
        jx._install_dir(self.src, self.dst, protect_name="Source")
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "payload.txt")))

    def test_refuses_textured_folder_without_character_json(self):
        os.makedirs(self.dst)
        open(os.path.join(self.dst, "model_textured.obj"), "w").close()
        with self.assertRaises(jc.JMPPEDError):
            jx._install_dir(self.src, self.dst, protect_name="Source")
        self.assertTrue(os.path.isfile(os.path.join(self.dst,
                                                    "model_textured.obj")))

    def test_refuses_source_model_folder(self):
        os.makedirs(self.dst)
        open(os.path.join(self.dst, "source.json"), "w").close()
        with self.assertRaises(jc.JMPPEDError):
            jx._install_dir(self.src, self.dst, protect_name="Source")


class SkeletonVariantTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmpped-skel-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _project(self, variants):
        path = os.path.join(self.root, "out", "source", "havana", "skeleton",
                            "skeleton.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"variants": {v: {} for v in variants}}, f)
        return {"source": {"skeleton": {
            "json": "out/source/havana/skeleton/skeleton.json"}}}

    def test_prefers_jericho(self):
        project = self._project(["tanner", "jericho"])
        self.assertEqual(jx._skeleton_variant(self.root, project, {}),
                         "jericho")

    def test_falls_back_to_first_variant(self):
        project = self._project(["tanner"])
        self.assertEqual(jx._skeleton_variant(self.root, project, {}),
                         "tanner")

    def test_defaults_when_skeleton_missing(self):
        self.assertEqual(jx._skeleton_variant(self.root, {}, {}), "jericho")


if __name__ == "__main__":
    unittest.main()
