"""dentgen: zones, damage levels and the LUMP_CAR_DENTING layout."""
import struct
import unittest

import dentgen


class P(object):
    def __init__(self, tset, tid, v, uv):
        self.texture_set, self.texture_id, self.v, self.uv = tset, tid, v, uv


class M(object):
    def __init__(self, verts, polys):
        self.vertices, self.polys = verts, polys


class DentgenTest(unittest.TestCase):
    def test_zones_match_bcollide(self):
        # model -z is the front; x > 0 is the 0/5/4 side
        self.assertEqual(dentgen.zone_of(10, -300, 300), 0)
        self.assertEqual(dentgen.zone_of(-10, -300, 300), 1)
        self.assertEqual(dentgen.zone_of(-10, 0, 300), 2)
        self.assertEqual(dentgen.zone_of(-10, 300, 300), 3)
        self.assertEqual(dentgen.zone_of(10, 300, 300), 4)
        self.assertEqual(dentgen.zone_of(10, 0, 300), 5)

    def _make_page_and_model(self):
        """One page, two polys. Poly A (u 0..15) has art only at +128.
        Poly B (u 40..55) has no art beside it."""
        words = [[0] + [0x7FFF] * 15]
        idx = bytearray(65536)
        for v in range(16):
            for u in range(128, 144):
                idx[v * 256 + u] = 1
            for u in range(0, 16):
                idx[v * 256 + u] = 2
        verts = [(10, 0, -300), (20, 0, -300), (10, 10, -280),
                 (-10, 0, 300), (-20, 0, 300), (-10, 10, 280)]
        a = P(1, 0, (0, 1, 2), ((0, 0), (15, 0), (0, 15)))
        b = P(1, 0, (3, 4, 5), ((40, 40), (55, 40), (40, 55)))
        return words, idx, verts, a, b

    def _build(self, damage_modes=None):
        words, idx, verts, a, b = self._make_page_and_model()
        orig_decode, orig_page = dentgen.decode_clean_models, dentgen.page_from_entry
        dentgen.decode_clean_models = lambda body: {1: M(verts, [a, b])}
        dentgen.page_from_entry = lambda data: (words, bytes(idx))
        try:
            body, report = dentgen.build_dent_body(b"", {1: b"page"}, damage_modes=damage_modes)
        finally:
            dentgen.decode_clean_models = orig_decode
            dentgen.page_from_entry = orig_page
        return body, report

    def test_levels_d1_mode_default(self):
        """Default "d1" mode: every textured poly gets D=1 (+64 light, +128 heavy)."""
        body, report = self._build()   # no damage_modes = "d1" default
        off = struct.unpack_from("<13i", body, 8)[1]
        block = body[off:off + dentgen.BLOCK_BYTES]
        levels = block[dentgen.NUM_ZONES * (dentgen.ZONE_VERTS + dentgen.ZONE_POLYS):]
        # Both polys have UVs, so both get D=1 regardless of page art
        self.assertEqual(levels[0], 1)   # D=1: +64 light, +128 heavy
        self.assertEqual(levels[1], 1)   # D=1: even though no art at +64/+128
        self.assertEqual(report[1]["shift_64_128"], 2)
        self.assertEqual(report[1]["still"], 0)

    def test_levels_auto_mode_follows_art(self):
        """Explicit "auto" mode: D is chosen by probing the page."""
        body, report = self._build(damage_modes={1: "auto"})
        off = struct.unpack_from("<13i", body, 8)[1]
        block = body[off:off + dentgen.BLOCK_BYTES]
        levels = block[dentgen.NUM_ZONES * (dentgen.ZONE_VERTS + dentgen.ZONE_POLYS):]
        self.assertEqual(levels[0], 0)   # +128 only: heavy damage shows it
        self.assertEqual(levels[1], 2)   # no art: never moves
        polys_zone0 = block[dentgen.NUM_ZONES * dentgen.ZONE_VERTS:][:dentgen.ZONE_POLYS]
        self.assertEqual(polys_zone0[0], 0)   # poly A is front, x > 0
        self.assertEqual(report[1]["still"], 1)


if __name__ == "__main__":
    unittest.main()
