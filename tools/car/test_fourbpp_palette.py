"""fourbpp.paletteize: 5-bit exact fit, no false STP black, no black padding."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import fourbpp

W = H = 16


class PaletteizeTest(unittest.TestCase):
    def test_16_shades_in_256_colour_png_stay_exact(self):
        # 16 5-bit shades, each smeared over 8 low-bit variants -> 128 8-bit colours
        base = [(s * 16 + 8, 255 - s * 16, 40 + s * 8) for s in range(16)]
        base = [fourbpp.snap555(c) for c in base]
        px = []
        for i in range(W * H):
            r, g, b = base[i % 16]
            j = (i // 16) % 8 - 4 if (i // 16) % 8 < 4 else (i // 16) % 8 - 4
            px.append((max(0, min(255, r + j)), max(0, min(255, g + j)), max(0, min(255, b + j))))
        self.assertGreater(len(set(px)), 16)
        colours, idx = fourbpp.paletteize(px, W, H)
        self.assertEqual(sorted(set(colours)), sorted(set(base)))
        for p, i in zip(px, idx):
            self.assertEqual(colours[i], fourbpp.snap555(p))

    def test_near_black_is_not_stp(self):
        px = [(5, 5, 5)] * (W * H // 2) + [(200, 30, 30)] * (W * H // 2)
        colours, idx = fourbpp.paletteize(px, W, H)
        row = fourbpp.rgb_to_clut(colours)
        self.assertNotEqual(fourbpp.rgb555(colours[idx[0]]), 0)
        self.assertNotIn(b"\x00\x00", [row[k:k + 2] for k in range(0, 32, 2)])

    def test_near_black_not_stp_in_median_cut(self):
        px = [(2, 1, 3)] * 64 + [((i * 7) % 256, (i * 13) % 256, (i * 29) % 256)
                                  for i in range(W * H - 64)]
        colours, idx = fourbpp.paletteize(px, W, H)
        for k in range(64):
            self.assertNotEqual(fourbpp.rgb555(colours[idx[k]]), 0)
        self.assertNotIn((0, 0, 0), colours)

    def test_true_black_stays_black(self):
        px = [(0, 0, 0)] * 128 + [(250, 250, 250)] * 128
        colours, idx = fourbpp.paletteize(px, W, H)
        self.assertEqual(colours[idx[0]], (0, 0, 0))

    def test_padding_is_not_pure_black(self):
        px = [(120, 60, 200)] * (W * H)
        colours, _ = fourbpp.paletteize(px, W, H)
        self.assertEqual(len(colours), 16)
        self.assertNotIn((0, 0, 0), colours)

    def test_round_to_nearest(self):
        self.assertEqual(fourbpp.snap555((7, 12, 255)), (8, 16, 248))
        self.assertEqual(fourbpp.snap555((0, 0, 0)), (0, 0, 0))
        self.assertEqual(fourbpp.snap555((1, 0, 0)), (8, 8, 8))


if __name__ == "__main__":
    unittest.main()
