#!/usr/bin/env python3
"""Preview size: Driver 1 meshes land in the Driver 2 band, once.

    python -m unittest test_preview_scale -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import preview


class ScaleTests(unittest.TestCase):
    def test_d1_median_lands_in_the_d2_band(self):
        # docs/DRIVER1.md §8: 1104 * 2048/3000 = 754 against D2's 752.
        length = 1104 * preview.BAKE_SCALE
        width = 400 * preview.BAKE_SCALE
        self.assertAlmostEqual(length, 754, delta=2)
        self.assertAlmostEqual(width, 273, delta=2)

    def test_redbuick_obj_uses_the_d1_scale(self):
        # OBJ longest axis ~1104, handling colBox half-length ~377 (engine).
        scale = preview.infer_bake_scale((400, 254, 1104), (136, 88, 377))
        self.assertAlmostEqual(scale, preview.BAKE_SCALE)

    def test_an_engine_sized_obj_is_not_scaled_again(self):
        scale = preview.infer_bake_scale((284, 176, 752), (142, 88, 376))
        self.assertEqual(scale, 1.0)

    def test_a_long_axis_without_a_colbox_is_still_d1(self):
        self.assertAlmostEqual(preview.infer_bake_scale((1104, 400, 254)),
                               preview.BAKE_SCALE)
        self.assertEqual(preview.infer_bake_scale((752, 284, 176)), 1.0)
        self.assertEqual(preview.infer_bake_scale((4.2, 1.5, 1.8)), 265.0)

    def test_uv_one_samples_the_last_texel_not_the_wrap(self):
        # engine texel 255 is exported as 1.0. GL u=1 wraps to texel 0.
        u, v = preview.gl_uv(1.0, 0.0, 256, 256)
        self.assertAlmostEqual(u, 255.5 / 256.0)
        self.assertAlmostEqual(v, 0.5 / 256.0)
        mid, _ = preview.gl_uv(128 / 255.0, 0.0, 256, 256)
        self.assertAlmostEqual(mid, 128.5 / 256.0, places=4)

    def test_unscaled_d1_draws_larger_than_the_d2_reference(self):
        d2 = preview.reference_span()
        d1 = preview.projected_span(
            [preview._project(v) for v in preview.median_box_corners((400, 254, 1104))])
        scaled_box = tuple(v * preview.BAKE_SCALE for v in (400, 254, 1104))
        scaled = preview.projected_span(
            [preview._project(v) for v in preview.median_box_corners(scaled_box)])

        self.assertGreater(d1 / d2, 1.3)
        self.assertLess(abs(scaled / d2 - 1.0), 0.08)


if __name__ == "__main__":
    unittest.main()
