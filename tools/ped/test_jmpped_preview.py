#!/usr/bin/env python3
"""Preview character-selection tests."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_entries as je
import jmpped_preview as jp

ARCHIVE = os.path.join(HERE, "out", "build", "JMPPED.LEV")


class PreviewSelectionTests(unittest.TestCase):
    def test_default_is_last_character_and_explicit_selects(self):
        if not os.path.isfile(ARCHIVE):
            self.skipTest("built archive not present")
        manifest = je.load_manifest(HERE)
        expected = manifest["characters"][-1]["name"]
        models, name = jp.load_models(ARCHIVE)
        self.assertEqual(name, expected)
        models0, name0 = jp.load_models(ARCHIVE, "Jericho")
        self.assertEqual(name0, "Jericho")
        self.assertNotEqual(models[1]["verts"].tobytes(),
                            models0[1]["verts"].tobytes())


if __name__ == "__main__":
    unittest.main()
