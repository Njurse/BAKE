#!/usr/bin/env python3
"""Verify that the one-time rename moved paths but not recorded state.

The recorded *_sha256 values are change detectors. The migration must carry
them over verbatim, even when the file on disk no longer matches (a pending
model edit), and the edited model must survive byte-for-byte.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_core as jc
import jmpped_entries as je
import jmpped_model as jmodel
import jmpped_workspace as jws


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _hashes(data) -> list[str]:
    out: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("_sha256") and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_hashes(value))
    elif isinstance(data, list):
        for item in data:
            out.extend(_hashes(item))
    return out


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class MigrationStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = os.path.join(HERE, "project.json")
        if not os.path.isfile(project):
            raise unittest.SkipTest("workspace has not been migrated")
        cls.project = _load(project)
        backups = sorted(glob.glob(os.path.join(
            HERE, ".state", "backups", "pre-rename-*")))
        if not backups:
            raise unittest.SkipTest("no pre-rename backup")
        cls.backup = backups[-1]
        cls.old = _load(os.path.join(cls.backup, "jmppeds", "v3",
                                     "jmpped.json"))
        cls.level_dir = os.path.join(
            HERE, cls.project["source"]["project"].replace("/", os.sep))

    def _character_files(self):
        for c in self.project["characters"]:
            yield c, _load(os.path.join(HERE, c["folder"], "character.json"))

    def _source_files(self):
        for name in self.project["source_models"]:
            yield name, _load(os.path.join(
                self.level_dir, "source_models", name, "source.json"))

    def _has_split(self):
        return any((c.get("model") or {}).get("split")
                   for c in self.project.get("characters", []))

    def test_all_new_hashes_are_old_values(self):
        if self._has_split():
            self.skipTest("workspace has been split since the rename")
        old = set(_hashes(self.old))
        new = list(_hashes(self.project))
        for _c, data in self._character_files():
            new.extend(_hashes(data))
        for _name, data in self._source_files():
            new.extend(_hashes(data))
        missing = sorted(set(new) - old)
        self.assertFalse(
            missing,
            "new manifests contain hashes that were not in the old manifest: %s"
            % missing[:5])

    def test_character_model_hashes_preserved(self):
        if self._has_split():
            self.skipTest("workspace has been split since the rename")
        old = {e["name"]: e for e in self.old["entries"]}
        for c in self.project["characters"]:
            before = old[c["name"]]["bindpose"]
            self.assertEqual(c["model"]["sha256"], before["edit_sha256"])
            self.assertEqual(c["engine_reference"]["sha256"],
                             before["raw_sha256"])

    def test_source_model_hashes_preserved(self):
        for name, rec in self.project["source_models"].items():
            before = self.old["peds"][name]["bindpose"]
            self.assertEqual(rec["model"]["sha256"], before["edit_sha256"])
            self.assertEqual(rec["engine_reference"]["sha256"],
                             before["raw_sha256"])

    def test_model_bytes_preserved(self):
        old = {e["name"]: e for e in self.old["entries"]}
        for c in self.project["characters"]:
            if (c.get("model", {}).get("uv_space") == "crop"
                    or c.get("model", {}).get("format") == "textured-obj-v1"):
                # The page-space model is generated from the textured model.
                continue
            before = os.path.join(self.backup, "jmppeds", "v3",
                                  old[c["name"]]["bindpose"]["edit"]
                                  .replace("/", os.sep))
            after_rel = c["model"].get("page_path") or c["model"]["path"]
            after = os.path.join(HERE, after_rel.replace("/", os.sep))
            self.assertTrue(os.path.isfile(after), after)
            self.assertEqual(_sha(after), _sha(before), after)
        for name, rec in self.project["source_models"].items():
            before = os.path.join(self.backup, "jmppeds", "v3",
                                  self.old["peds"][name]["bindpose"]["edit"]
                                  .replace("/", os.sep))
            after = os.path.join(HERE, rec["model"]["path"].replace("/", os.sep))
            self.assertTrue(os.path.isfile(after), after)
            self.assertEqual(_sha(after), _sha(before), after)

    def test_old_layout_is_gone(self):
        self.assertFalse(os.path.isdir(os.path.join(HERE, "out", "jmppeds")))
        self.assertTrue(os.path.isdir(os.path.join(HERE, "characters")))
        self.assertTrue(os.path.isdir(self.level_dir))

    def test_old_layout_is_refused_with_migrate_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "out", "jmppeds", "v3")
            os.makedirs(old)
            with open(os.path.join(old, "jmpped.json"), "w",
                      encoding="utf-8") as f:
                f.write("{}")
            with self.assertRaises(jc.JMPPEDError) as ctx:
                je.load_manifest(tmp)
            self.assertIn("migrate", str(ctx.exception))
            with self.assertRaises(jc.JMPPEDError) as ctx2:
                jws.ensure_project(tmp)
            self.assertIn("migrate", str(ctx2.exception))
            self.assertTrue(jws.status(tmp)["migrate_required"])


class BakeByteIdentityTests(unittest.TestCase):
    def test_no_edit_bake_is_byte_identical(self):
        project = os.path.join(HERE, "project.json")
        base = os.path.join(HERE, "JMPPED.LEV")
        if not os.path.isfile(project) or not os.path.isfile(base):
            self.skipTest("no project/archive in this checkout")
        manifest = je.load_manifest(HERE)
        if any(jmodel.model_pending(HERE, c, manifest)
               for c in manifest.get("characters", [])):
            self.skipTest("combined model is pending; the baseline test does not apply")
        import jmpped_bake
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "JMPPED.LEV")
            self.assertEqual(jmpped_bake.bake(HERE, dest=dest), 0)
            with open(dest, "rb") as f:
                rebuilt = f.read()
            with open(base, "rb") as f:
                original = f.read()
            self.assertEqual(rebuilt, original)


if __name__ == "__main__":
    unittest.main()
