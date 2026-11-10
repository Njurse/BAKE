#!/usr/bin/env python3
"""make_write_scripts.py keeps a bank's knobs and points at the right tree.

    python -m unittest test_make_write_scripts -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_write_scripts


def _bank(root, *parts, city="NICE"):
    folder = os.path.join(root, *parts)
    os.makedirs(folder)
    with open(os.path.join(folder, "bank.json"), "w", encoding="utf-8") as fh:
        json.dump({"city": city, "lev": city + ".LEV",
                   "declare": {"donor": {"city": "CHICAGO"}}}, fh)
    return folder


def _release(root):
    os.makedirs(os.path.join(root, "src_rebuild", "bin", "Release_dev"))
    return os.path.join(root, "src_rebuild", "bin", "Release_dev")


class PreserveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="writescr-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.bank = _bank(self.dir, "NICE")

    def _seed(self, mode="winding", args="--scale 1", preview="--mute"):
        text = "\n".join([
            'if "%%MODE%%"=="" set "MODE=%s"' % mode,
            'python "%%CAR%%\\carbank_bake.py" "%%BANK%%\\bank.json" '
            '-o "%%OUT%%" %s %%FLIP%%' % args,
            'python "%%CAR%%\\preview_car.py" --stop-file "%%STOPF%%" %s' % preview,
            "",
        ])
        with open(os.path.join(self.bank, "rebake.bat"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _rebake(self):
        with open(os.path.join(self.bank, "rebake.bat"), encoding="utf-8") as fh:
            return fh.read()

    def test_force_keeps_mode_args_and_preview_when_flags_are_omitted(self):
        self._seed()
        make_write_scripts.write(self.bank, "NICE", force=True)
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=winding"', text)
        self.assertIn('-o "%OUT%" --scale 1 %FLIP%', text)
        self.assertIn('--stop-file "%STOPF%" --mute', text)
        with open(os.path.join(self.bank, "preview.bat"), encoding="utf-8") as fh:
            preview = fh.read()
        self.assertIn("--timeout 0 --mute", preview)

    def test_an_explicit_flag_overrides_the_bank(self):
        self._seed()
        make_write_scripts.write(self.bank, "NICE", mode="plain",
                                 args="--scale 9", preview="--flip-y",
                                 force=True)
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=plain"', text)
        self.assertIn('-o "%OUT%" --scale 9 %FLIP%', text)
        self.assertIn('--stop-file "%STOPF%" --flip-y', text)
        self.assertNotIn('set "MODE=winding"', text)
        self.assertNotIn("--scale 1", text)
        self.assertNotIn("--mute", text)

    def test_one_explicit_flag_leaves_the_other_two(self):
        self._seed()
        make_write_scripts.write(self.bank, "NICE", mode="normals", force=True)
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=normals"', text)
        self.assertIn('-o "%OUT%" --scale 1 %FLIP%', text)
        self.assertIn('--stop-file "%STOPF%" --mute', text)

    def test_cli_flags_override_and_omitted_flags_do_not(self):
        self._seed()
        script = os.path.join(os.path.dirname(__file__), "make_write_scripts.py")
        bank_json = os.path.join(self.bank, "bank.json")
        subprocess.check_call([sys.executable, script, bank_json, "--force",
                               "--mode", "plain"], cwd=self.dir,
                              stdout=subprocess.DEVNULL)
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=plain"', text)
        self.assertIn('-o "%OUT%" --scale 1 %FLIP%', text)
        self.assertIn("--mute", text)
        subprocess.check_call([sys.executable, script, bank_json, "--force",
                               "--args", "--scale", "2",
                               "--preview", "--flip-y"], cwd=self.dir,
                              stdout=subprocess.DEVNULL)
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=plain"', text)
        self.assertIn('-o "%OUT%" --scale 2 %FLIP%', text)
        self.assertIn('--stop-file "%STOPF%" --flip-y', text)
        self.assertNotIn("--mute", text)

    def test_a_new_bank_gets_the_defaults(self):
        make_write_scripts.write(self.bank, "NICE", args="--scale 1")
        text = self._rebake()
        self.assertIn('if "%MODE%"=="" set "MODE=plain"', text)
        self.assertIn('-o "%OUT%" --scale 1 %FLIP%', text)
        self.assertIn('--stop-file "%STOPF%" --flip-y', text)


class GameTreeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="writescr-game-")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_a_bank_inside_release_dev_mirrors_itself(self):
        root = os.path.join(self.dir, "REDRIVER2")
        release = _release(root)
        # A previous doubled install left this decoy. It must not become the root.
        os.makedirs(os.path.join(release, "src_rebuild", "bin", "Release_dev",
                                 "JERICHO"))
        bank = _bank(release, "JERICHO", "MODS", "civviecops", "CIVICOPS",
                     city="CIVICOPS")
        mirror, exedir = make_write_scripts.mirror_and_exedir(bank)
        self.assertEqual(mirror, "%BANK%")
        self.assertNotIn("src_rebuild", exedir.replace("%BANK%\\", ""))
        suffix = exedir.replace("%BANK%\\", "").replace("\\", os.sep)
        self.assertEqual(os.path.normcase(os.path.normpath(os.path.join(bank, suffix))),
                         os.path.normcase(os.path.normpath(release)))

        make_write_scripts.write(bank, "CIVICOPS", force=True)
        for name in ("rebake.bat", "playtest.bat", "wizard.bat"):
            with open(os.path.join(bank, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn('set "MIRROR=%BANK%"', text)
            line = next(ln for ln in text.splitlines() if ln.startswith('set "EXEDIR='))
            self.assertNotIn("src_rebuild", line)
            self.assertNotIn("src_rebuild\\bin\\Release_dev\\JERICHO", text)

    def test_a_bank_outside_the_tree_still_mirrors_under_mods(self):
        root = os.path.join(self.dir, "REDRIVER2")
        _release(root)
        tools = os.path.join(root, "JERICHO", "MODS", "bake", "tools", "car")
        os.makedirs(tools)
        bank = _bank(self.dir, "moved", "NICE")
        old = make_write_scripts.HERE
        make_write_scripts.HERE = tools
        try:
            mirror, exedir = make_write_scripts.mirror_and_exedir(bank)
        finally:
            make_write_scripts.HERE = old
        self.assertTrue(exedir.startswith("%BANK%\\"))
        self.assertIn("src_rebuild\\bin\\Release_dev", exedir)
        self.assertTrue(mirror.endswith("\\JERICHO\\MODS\\NICE"), mirror)
        self.assertNotIn("Release_dev\\src_rebuild", mirror)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="writescr-res-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.bank = _bank(self.dir, "NICE")
        make_write_scripts.write(self.bank, "NICE", force=True)

    def _text(self, name):
        with open(os.path.join(self.bank, name), encoding="utf-8") as fh:
            return fh.read()

    def test_generated_wizard_names_the_car_fallback_and_relaunches(self):
        wizard = self._text("wizard.bat")
        self.assertIn(r"bake\tools\car\make_write_scripts.py", wizard)
        self.assertIn(r'call "%~dp0resolve_paths.bat"', wizard)
        self.assertIn(r'call "%~f0"', wizard)
        self.assertIn("exit /b 0", wizard.split(":refresh", 1)[1])

    def test_the_walk_lives_in_one_helper(self):
        helper = self._text("resolve_paths.bat")
        self.assertIn(r"bake\tools\car\make_write_scripts.py", helper)
        self.assertIn("JERICHO_BAKE", helper)
        self.assertIn("JERICHO_GAME", helper)
        self.assertIn(":car_walk", helper)
        self.assertIn("src_rebuild\\bin\\Release_dev", helper)
        name_at = helper.index('if /i "%_LEAF%"=="Release_dev"')
        child_at = helper.index('set "EXEDIR=%_WALK%\\src_rebuild\\bin\\Release_dev"')
        self.assertLess(name_at, child_at)
        for name in ("rebake.bat", "playtest.bat", "preview.bat", "wizard.bat"):
            body = self._text(name)
            self.assertNotIn(":car_walk", body)
            self.assertIn("resolve_paths.bat", body)

    def test_a_missing_game_does_not_fail_the_bake(self):
        rebake = self._text("rebake.bat")
        self.assertIn('if "%GAME_MISSING%"=="1"', rebake)
        self.assertIn("skipped install and city registration", rebake)
        self.assertNotIn("FAILED: no game at", rebake)
        self.assertIn("JERICHO_GAME", rebake)


if __name__ == "__main__":
    unittest.main()
