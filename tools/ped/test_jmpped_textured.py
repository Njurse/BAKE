#!/usr/bin/env python3
"""Textured-model conversion tests."""

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
import jmpped_entries as je
import jmpped_images as ji
import jmpped_models as jm
import jmpped_textured as jt
import jmpped_bake as jb


def _copy_character(root):
    fixture = os.path.join(HERE, ".state", "backups",
                           "pre-combine-fixture", "jerichod3")
    src = fixture if os.path.isdir(fixture) else os.path.join(
        HERE, "characters", "jerichod3")
    dst = os.path.join(root, "characters", "test")
    shutil.copytree(src, dst)
    character = jc.load_json(os.path.join(dst, "character.json"))
    je.rewrite_paths(character, "characters/jerichod3", "characters/test")
    jc.save_json(os.path.join(dst, "character.json"), character)
    jc.save_json(os.path.join(root, "project.json"), {
        "project_version": 2,
        "format_version": jc.VERSION_V5,
        "source": {"skeleton": {
            "json": "out/source/havana/skeleton/skeleton.json"}},
        "characters": [character],
    })
    return character


class HeadMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = je.load_manifest(HERE)
        cls.character = je.character(cls.manifest, "Jericho Driv3r")
        cls.page_abs = os.path.join(HERE, "characters", "jerichod3", "model.obj")
        cls.groups = jt._read_page_groups(HERE, cls.character, cls.page_abs)

    def test_texture_groups(self):
        groups = jt.texture_groups(self.character)
        body = groups[(26, 9)]
        self.assertTrue(body["shared"])
        self.assertEqual(body["rect"], [112, 128, 16, 64])
        self.assertEqual(len(body["members"]), 5)
        legs = groups[(3, 12)]
        self.assertTrue(legs["shared"])
        self.assertEqual(legs["rect"], [128, 0, 49, 64])
        self.assertEqual(len(legs["members"]), 6)
        hands = groups[(3, 2)]
        self.assertTrue(hands["shared"])
        self.assertEqual(hands["rect"], [194, 51, 29, 13])
        self.assertEqual(len(hands["members"]), 2)
        for key in ((27, 22), (27, 23), (27, 24), (27, 25)):
            self.assertFalse(groups[key]["shared"], key)
        shared = jt.shared_textures(self.character)
        self.assertEqual(len(shared), 3)
        self.assertEqual({tuple(g["members"]) for g in shared}, {
            ("JERI_TORSO", "JERI_U_ARM_LEFT", "JERI_L_ARM_LEFT",
             "JERI_U_ARM_RIGHT", "JERI_L_ARM_RIGHT"),
            ("THIGH_LEFT", "CALF_LEFT", "FOOT_LEFT",
             "THIGH_RIGHT", "CALF_RIGHT", "FOOT_RIGHT"),
            ("HAND_LEFT", "HAND_RIGHT"),
        })

    def test_material_names_round_trip(self):
        self.assertEqual(jt.part_material("JERI_HEAD", 27, 24),
                         "JERI_HEAD__s27_t24")
        self.assertEqual(jt.parse_material("JERI_HEAD__s27_t24"),
                         ("JERI_HEAD", 27, 24))
        self.assertEqual(jt.parse_material("JERI_HEAD__s27_t24.001"),
                         ("JERI_HEAD", 27, 24))
        self.assertIsNone(jt.parse_material("s27_t24"))

    def test_head_crop_round_trip(self):
        for texture, rect in ((24, [144, 120, 16, 24]),
                              (25, [128, 192, 16, 24])):
            crop = jt._crop(self.character, "JERI_HEAD", 27, texture)
            self.assertIsNotNone(crop)
            self.assertEqual(list(crop["rect"]), rect)
            for uv in self._head_uvs(texture):
                local = jt.page_to_crop(uv, rect)
                self.assertTrue(jt._inside(local), (texture, uv, local))
                back = jt.crop_to_page(local, rect)
                self.assertAlmostEqual(back[0], uv[0], places=6)
                self.assertAlmostEqual(back[1], uv[1], places=6)

    def test_small_uv_overshoot_is_tolerated(self):
        # The Jericho Driv3r glasses sit a pixel or two outside their 20x8 crop;
        # that must import (clipped), while a genuinely wrong UV still fails.
        rect = [0, 0, 20, 8]
        self.assertTrue(jt._inside((0.023472, -0.127226), rect))
        self.assertTrue(jt._inside((1.001004, -0.235121), rect))
        self.assertFalse(jt._inside((1.5, 0.5), rect))

    def test_nonblack_colour_avoids_the_transparent_black_entry(self):
        clut = [(0, 0, 0), (8, 8, 8), (200, 0, 0)] + [(0, 0, 0)] * 13
        self.assertEqual(ji.nearest_clut_index((1, 1, 1), clut), 1)
        self.assertEqual(ji.nearest_clut_index((0, 0, 0), clut), 0)
        self.assertNotEqual(ji.nearest_clut_index((100, 100, 100), clut), 0)

    def test_paletteize_keeps_nonblack_texels_off_black(self):
        # More than 16 colours forces the median-cut path, where Pillow can fold
        # a near-black texel into the pure-black entry.
        pixels = ([(0, 0, 0)] * 4 + [(1, 1, 1), (2, 2, 2), (3, 3, 3)]
                  + [(i * 10, 0, 0) for i in range(1, 20)])
        colors, patch = jb._paletteize_crop(pixels, len(pixels), 1)
        for pixel, index in zip(pixels, patch):
            if pixel != (0, 0, 0):
                self.assertNotEqual(colors[index], (0, 0, 0), pixel)

    def _head_uvs(self, texture):
        material = "s%d_t%d" % (jt._target_set(self.character, 25), texture)
        out = []
        for face in self.groups["JERI_HEAD"]["faces"]:
            if face[0][2] == material:
                out.extend(uv for _vi, uv, _mat in face)
        self.assertTrue(out, material)
        return out

    def test_part_materials_mark_a_localized_model(self):
        localized = {"JERI_HEAD": {"faces": [[
            (0, (0.0, 0.0), "JERI_HEAD__s27_t24")]]}}
        page_space = {"JERI_HEAD": {"faces": [[
            (0, (0.0, 0.0), "s25_t24")]]}}
        self.assertTrue(jt._has_part_material(localized))
        self.assertFalse(jt._has_part_material(page_space))

    def test_shared_material_maps_to_part_crops(self):
        for part in ("JERI_TORSO", "JERI_U_ARM_LEFT", "JERI_U_ARM_RIGHT"):
            target = jt._target_set(self.character, 23)
            materials = {face[0][2] for face in self.groups[part]["faces"]}
            self.assertTrue("s23_t9" in materials or "s26_t9" in materials,
                            materials)
            crop = jt._crop(self.character, part, target, 9)
            self.assertIsNotNone(crop, part)
            self.assertTrue("_set26_t9.png" in crop["rgb"]
                            or "s26_t9.png" in crop["rgb"], crop["rgb"])


class TempExportTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmpped-textured-")
        self.addCleanup(shutil.rmtree, self.root, True)
        src = os.path.join(HERE, "characters", "jerichod3")
        dst = os.path.join(self.root, "characters", "test")
        shutil.copytree(src, dst)
        self.character = jc.load_json(os.path.join(dst, "character.json"))
        je.rewrite_paths(self.character, "characters/jerichod3",
                         "characters/test")
        jc.save_json(os.path.join(dst, "character.json"), self.character)
        jc.save_json(os.path.join(self.root, "project.json"), {
            "project_version": 2,
            "format_version": 5,
            "source": {"skeleton": {"json": "out/source/havana/skeleton/skeleton.json"}},
            "characters": [self.character],
        })
        self.page_abs = os.path.join(dst, "model.obj")
        self.textured = os.path.join(dst, "model_textured.obj")
        self.mtl = os.path.join(dst, "model_textured.mtl")
        self.assertEqual(jt.export_textured(self.root, self.character["name"]), 0)
        self.character = jc.load_json(os.path.join(dst, "character.json"))

    def test_textured_files_and_material_paths(self):
        self.assertTrue(os.path.isfile(self.textured))
        self.assertTrue(os.path.isfile(self.mtl))
        obj = open(self.textured, encoding="utf-8").read()
        mtl = open(self.mtl, encoding="utf-8").read()
        self.assertIn("JERI_HEAD", obj)
        self.assertTrue("JERI_HEAD__s25_t24" in obj or "JERI_HEAD__s27_t24" in obj)
        self.assertTrue("JERI_HEAD__s25_t25" in obj or "JERI_HEAD__s27_t25" in obj)
        self.assertTrue("JERI_TORSO__s23_t9" in obj or "JERI_TORSO__s26_t9" in obj)
        self.assertTrue("JERI_U_ARM_LEFT__s23_t9" in obj
                        or "JERI_U_ARM_LEFT__s26_t9" in obj)
        self.assertTrue("JERI_U_ARM_RIGHT__s23_t9" in obj
                        or "JERI_U_ARM_RIGHT__s26_t9" in obj)
        self.assertIn("Glasses", obj)
        self.assertIn("Material.001", obj)
        self.assertTrue("map_Kd textures/crops/JERI_HEAD_set27_t22.png" in mtl
                        or "map_Kd textures/extra_assets/glasses.png" in mtl)
        self.assertTrue("map_Kd textures/crops/JERI_HEAD_set27_t23.png" in mtl
                        or "map_Kd textures/extra_assets/glasses_side.png" in mtl)
        self.assertTrue("JERI_HEAD_set27_t24" in mtl
                        or "JERI_HEAD_set25_t24" in mtl)
        self.assertTrue("JERI_HEAD_set27_t25" in mtl
                        or "JERI_HEAD_set25_t25" in mtl)
        char_dir = os.path.join(self.root, "characters", "test")
        for line in mtl.splitlines():
            if line.startswith("map_Kd "):
                self.assertTrue(os.path.isfile(os.path.join(char_dir,
                                                            line[7:])),
                                line)

    def test_schema_and_check(self):
        model = self.character["model"]
        self.assertEqual(model["path"], "characters/test/model_textured.obj")
        self.assertEqual(model["page_path"], "characters/test/model.obj")
        self.assertEqual(model["uv_space"], "crop")
        self.assertEqual(model["format"], jt.FORMAT)
        self.assertTrue(model["sha256"])
        self.assertTrue(model["page_sha256"])
        self.assertEqual(jt.check_uvs(self.root, self.character["name"]), 0)

    def test_textured_round_trip_matches_page_model(self):
        page_groups = jt._read_page_groups(self.root, self.character,
                                           self.page_abs)
        groups = jt.read_textured(self.root, self.character)
        for part in ("JERI_HEAD", "JERI_TORSO", "THIGH_LEFT"):
            self.assertEqual(len(groups[part]["faces"]),
                             len(page_groups[part]["faces"]), part)
            page_uvs = sorted((round(uv[0], 4), round(uv[1], 4))
                              for face in page_groups[part]["faces"]
                              for _vi, uv, _mat in face)
            text_uvs = sorted((round(uv[0], 4), round(uv[1], 4))
                              for face in groups[part]["faces"]
                              for _vi, uv, _mat in face)
            self.assertEqual(text_uvs, page_uvs, part)

    def test_write_page_model_from_textured(self):
        groups = jt.read_textured(self.root, self.character)
        jt.write_page_model(self.root, self.character, groups)
        self.assertTrue(os.path.isfile(self.page_abs))
        page_groups = jm.read_combined_obj(
            self.page_abs,
            {p["part"] for p in self.character["parts"]} | {"HIPS"},
            blender=True)
        for part in ("JERI_HEAD", "JERI_TORSO"):
            self.assertEqual(len(page_groups[part]["faces"]),
                             len(groups[part]["faces"]), part)
        self.assertTrue(self.character["model"]["page_sha256"])

    def test_outside_uv_is_refused(self):
        text = open(self.textured, encoding="utf-8").read().splitlines()
        head = next(i for i, line in enumerate(text)
                    if line.startswith("o JERI_HEAD")
                    or line.startswith("g JERI_HEAD"))
        vt = next(i for i in range(head, len(text))
                  if text[i].startswith("vt "))
        text[vt] = "vt 1.500000 0.500000"
        bad = os.path.join(os.path.dirname(self.textured),
                           "model_textured_bad.obj")
        with open(bad, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(text) + "\n")
        self.character["model"]["path"] = "characters/test/model_textured_bad.obj"
        _groups, _stats, issues = jt.scan_textured(self.root, self.character)
        self.assertTrue(issues)
        with self.assertRaises(jc.JMPPEDError):
            jt.read_textured(self.root, self.character)


class CombineTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmpped-combine-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.character = _copy_character(self.root)
        self.before_groups, _stats, issues = jt.scan_textured(
            self.root, self.character)
        self.assertEqual(issues, [])
        self.assertEqual(jt.combine_character(self.root,
                                             self.character["name"]), 0)
        self.character = jc.load_json(os.path.join(
            self.root, "characters", "test", "character.json"))

    def test_union_crops_overlay_member_edits(self):
        groups = jt.texture_groups(self.character)
        records = self.character["shared_textures"]
        self.assertEqual(len(records), 3)
        for record in records:
            key = (record["page_set"], record["texture"])
            group = groups[key]
            self.assertTrue(group["shared"])
            self.assertEqual(group["rect"], record["rect"])
            path = os.path.join(self.root, group["rgb"])
            self.assertTrue(os.path.isfile(path))
            w, h, pixels = ji.load_png_rgb(path)
            self.assertEqual((w, h), (record["rect"][2], record["rect"][3]))
            for index, (member, source) in enumerate(
                    zip(record["members"], record["source_rects"])):
                later = record["source_rects"][index + 1:]
                old = os.path.join(
                    self.root, "characters", "test", "textures", "crops",
                    "%s_set%d_t%d.png" % (member, key[0], key[1]))
                self.assertTrue(os.path.isfile(old), old)
                cw, ch, crop = ji.load_png_rgb(old)
                ox = source[0] - record["rect"][0]
                oy = source[1] - record["rect"][1]
                for yy in range(ch):
                    for xx in range(cw):
                        page_x = source[0] + xx
                        page_y = source[1] + yy
                        if any(l[0] <= page_x < l[0] + l[2]
                               and l[1] <= page_y < l[1] + l[3]
                               for l in later):
                            continue
                        self.assertEqual(
                            pixels[(oy + yy) * w + ox + xx],
                            crop[yy * cw + xx],
                            (member, xx, yy))

    def test_member_records_and_slots(self):
        groups = jt.texture_groups(self.character)
        for key in ((26, 9), (3, 12), (3, 2)):
            group = groups[key]
            for member in group["members"]:
                part = next(p for p in self.character["parts"]
                            if p["part"] == member["part"])
                crop = next(c for c in part["crops"]
                            if int(c["page_set"]) == key[0]
                            and int(c["texture"]) == key[1])
                self.assertEqual(crop["rgb"], group["rgb"])
                self.assertEqual(crop["rect"], group["rect"])
                self.assertEqual(crop["rgb_sha256"], "")
        head = next(p for p in self.character["parts"]
                    if p["part"] == "JERI_HEAD")
        for tex in (22, 23, 24, 25):
            crop = next(c for c in head["crops"]
                        if int(c["texture"]) == tex)
            self.assertIn("set27_t%d" % tex, crop["rgb"])
        self.assertEqual(len(self.character["shared_textures"]), 3)

    def test_uvs_keep_page_space(self):
        after, _stats, issues = jt.scan_textured(self.root, self.character)
        self.assertEqual(issues, [])
        for part in ("JERI_TORSO", "JERI_U_ARM_LEFT",
                     "THIGH_LEFT", "HAND_LEFT"):
            before = sorted((round(uv[0], 4), round(uv[1], 4))
                            for face in self.before_groups[part]["faces"]
                            for _vi, uv, _mat in face)
            now = sorted((round(uv[0], 4), round(uv[1], 4))
                         for face in after[part]["faces"]
                         for _vi, uv, _mat in face)
            self.assertEqual(before, now, part)

    def test_mtl_uses_shared_paths(self):
        mtl = open(os.path.join(self.root, "characters", "test",
                                "model_textured.mtl"),
                   encoding="utf-8").read()
        for name in ("s26_t9", "s3_t12", "s3_t2"):
            self.assertIn("map_Kd textures/crops/%s.png" % name, mtl)
        self.assertIn("map_Kd textures/crops/JERI_HEAD_set27_t24.png", mtl)
        self.assertIn("map_Kd textures/crops/JERI_HEAD_set27_t22.png", mtl)
        self.assertIn("map_d textures/extra_assets/glasses.png", mtl)

    def test_backup_and_check(self):
        self.assertEqual(jt.check_uvs(self.root, self.character["name"]), 0)
        backups = os.listdir(os.path.join(self.root, ".state", "backups"))
        self.assertTrue(any(n.startswith("combine-") for n in backups))


class NoCombineTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jmpped-nocombine-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.character = _copy_character(self.root)

    def test_per_part_output_intact(self):
        groups = jt.texture_groups(self.character)
        self.assertTrue(groups[(26, 9)]["shared"])
        for member in groups[(26, 9)]["members"]:
            part = next(p for p in self.character["parts"]
                        if p["part"] == member["part"])
            crop = next(c for c in part["crops"]
                        if int(c["page_set"]) == 26
                        and int(c["texture"]) == 9)
            self.assertIn("%s_set26_t9" % member["part"], crop["rgb"])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, "characters", "test", "textures", "crops",
            "s26_t9.png")))
        self.assertNotIn("shared_textures", self.character)
        mtl = open(os.path.join(self.root, "characters", "test",
                                "model_textured.mtl"),
                   encoding="utf-8").read()
        self.assertNotIn("map_Kd textures/crops/s26_t9.png", mtl)
        self.assertEqual(jt.check_uvs(self.root, self.character["name"]), 0)


class CropPaintTests(unittest.TestCase):
    def test_painted_crop_reaches_page_and_clut(self):
        import jmpped_bake as jb
        from PIL import Image

        root = tempfile.mkdtemp(prefix="jmpped-crop-")
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, "crop.png")
        Image.new("RGB", (2, 2), (255, 0, 0)).save(path)
        crop = {"rect": [0, 0, 2, 2], "rgb": "crop.png", "rgb_sha256": "",
                "palette_row": 0, "texture": 0, "page_set": 1}
        state = {"indices": bytes(65536), "cluts": [bytes(32)] * 16,
                 "display": 0, "full_page_touched": False,
                 "touched": False, "cluts_touched": False,
                 "path": path, "set": 1}
        jb._apply_crop(state, crop, root, [0])
        self.assertTrue(state["touched"])
        self.assertTrue(state["cluts_touched"])
        self.assertNotEqual(state["cluts"][0], bytes(32))

    def test_extra_material_crop_reaches_page_and_clut(self):
        import jmpped_bake as jb
        from PIL import Image

        root = tempfile.mkdtemp(prefix="jmpped-extra-crop-")
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, "glasses.png")
        Image.new("RGB", (2, 2), (0, 255, 0)).save(path)
        extra = {"material": "Glasses", "part": "JERI_HEAD",
                 "page_set": 1, "texture": 2, "rect": [0, 0, 2, 2],
                 "rgb": "glasses.png", "rgb_sha256": "",
                 "palette_row": 2}
        state = {"indices": bytes(65536), "cluts": [bytes(32)] * 16,
                 "display": 0, "full_page_touched": False,
                 "touched": False, "cluts_touched": False,
                 "path": path, "set": 1}
        character = {"name": "test", "parts": [], "extra_materials": [extra]}
        changed = jb._apply_character_crops(character, {1: state}, root)
        self.assertEqual(changed, 1)
        self.assertTrue(state["touched"])
        self.assertTrue(state["cluts_touched"])
        self.assertNotEqual(state["cluts"][2], bytes(32))


if __name__ == "__main__":
    unittest.main()
