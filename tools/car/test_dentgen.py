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

    def test_levels_d1_mode_asked_for_shifts_every_textured_poly(self):
        """"d1" mode: every textured poly gets D=1, art or no art."""
        body, report = self._build(damage_modes={1: "d1"})
        off = struct.unpack_from("<13i", body, 8)[1]
        block = body[off:off + dentgen.BLOCK_BYTES]
        levels = block[dentgen.NUM_ZONES * (dentgen.ZONE_VERTS + dentgen.ZONE_POLYS):]
        # Both polys have UVs, so both get D=1 regardless of page art
        self.assertEqual(levels[0], 1)   # D=1: +64 light, +128 heavy
        self.assertEqual(levels[1], 1)   # D=1: even though no art at +64/+128
        self.assertEqual(report[1]["shift_64_128"], 2)
        self.assertEqual(report[1]["still"], 0)
        self.assertEqual(report[1]["mode"], "d1")

    def test_levels_auto_mode_is_the_default(self):
        """The default mode probes the page: no damage art, no UV shift."""
        body, report = self._build()   # no damage_modes = "auto" default
        off = struct.unpack_from("<13i", body, 8)[1]
        block = body[off:off + dentgen.BLOCK_BYTES]
        levels = block[dentgen.NUM_ZONES * (dentgen.ZONE_VERTS + dentgen.ZONE_POLYS):]
        self.assertEqual(levels[0], 0)   # +128 only: heavy damage shows it
        self.assertEqual(levels[1], 2)   # no art: never moves
        polys_zone0 = block[dentgen.NUM_ZONES * dentgen.ZONE_VERTS:][:dentgen.ZONE_POLYS]
        self.assertEqual(polys_zone0[0], 0)   # poly A is front, x > 0
        self.assertEqual(report[1]["still"], 1)
        self.assertEqual(report[1]["mode"], "auto")


class StockDamageTest(unittest.TestCase):
    """A repackaged stock car takes the stock .DEN table, carried by geometry."""

    def _stock(self):
        verts = [(10, 0, -300), (20, 0, -300), (10, 10, -280),
                 (-10, 0, 300), (-20, 0, 300), (-10, 10, 280)]
        polys = [P(1, 0, (0, 1, 2), ((0, 0), (1, 0), (0, 1))),
                 P(1, 0, (3, 4, 5), ((0, 0), (1, 0), (0, 1)))]
        block = bytearray([0xFF] * dentgen.BLOCK_BYTES)
        zv, zp = dentgen.NUM_ZONES * dentgen.ZONE_VERTS, dentgen.ZONE_POLYS
        block[0:3] = bytes([0, 1, 2])          # zone 0 verts: the front ones
        block[zv:zv + 1] = bytes([0])          # zone 0 polys: poly 0
        levels = bytearray([2] * dentgen.LEVELS)
        levels[0], levels[1] = 0, 1            # stock: +128 heavy only / both
        block[zv + dentgen.NUM_ZONES * zp:] = levels
        return M(verts, polys), bytes(block)

    def test_reordered_mesh_with_a_new_part_takes_the_stock_levels(self):
        stock, block = self._stock()
        # the bake reordered the vertices (and moved one by 1 unit), swapped the
        # polys, and added a light bar on top
        verts = [(-10, 0, 300), (-20, 0, 300), (-10, 10, 280),
                 (10, 0, -300), (21, 0, -300), (10, 10, -280),
                 (0, 200, 0), (5, 200, 0), (0, 205, 0)]
        polys = [P(1, 0, (0, 1, 2), None), P(1, 0, (3, 4, 5), None),
                 P(1, 0, (6, 7, 8), None)]
        got = dentgen._stock_table(M(verts, polys), stock, block, 300)
        self.assertIsNotNone(got)
        zv, zp, levels, summary = got
        self.assertEqual(levels[0], 1)          # stock poly 1
        self.assertEqual(levels[1], 0)          # stock poly 0
        self.assertEqual(levels[2], 2)          # the light bar never shifts
        self.assertEqual(summary["matched_polys"], 2)
        self.assertEqual(summary["new_polys"], 1)
        self.assertEqual(sorted(zv[:3]), [3, 4, 5])   # stock zone-0 verts, renumbered
        self.assertEqual(zp[0], 1)                     # stock zone-0 poly, renumbered

    def test_a_different_car_falls_back(self):
        stock, block = self._stock()
        other = M([(500, 0, 0), (600, 0, 0), (500, 50, 0)],
                  [P(1, 0, (0, 1, 2), None)])
        self.assertIsNone(dentgen._stock_table(other, stock, block, 300))

    def test_stock_block_tolerates_the_975_byte_stride(self):
        den = bytearray(struct.pack("<13i", *([0] * 12 + [52])))
        den += bytes(dentgen.BLOCK_BYTES - 1)     # the last block, one byte short
        blk = dentgen.stock_block_for(bytes(den), 12)
        self.assertEqual(len(blk), dentgen.BLOCK_BYTES)
        self.assertEqual(blk[-1], 2)


if __name__ == "__main__":
    unittest.main()
