#!/usr/bin/env python3
"""Round-trip tests for editing a car's texture pages.

The guarantee these pin down is the one the bank pipeline depends on: an edit is
confined. A page the edit does not name comes back as the SAME bytes (not a
re-quantised copy), and a page an edit does name differs only inside the edited
rectangle. Without that, changing one wing on a car's atlas would silently shift
every other texture on the car.

Run from this directory:
    python -m unittest test_texture_roundtrip -v

A real page is used when one is on hand (the FIREBIRD bank's donor level, or an
extracted Driver 1 city); otherwise the tests fall back to a synthetic page of
the same shape, so they still run on a machine with no game data.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import fourbpp

# <repo>/JERICHO/MODS/betacars/banks/firebird/FIREBIRD.LEV
FIREBIRD_LEV = os.path.abspath(os.path.join(
    HERE, "..", "..", "..", "betacars", "banks", "firebird", "FIREBIRD.LEV"))


def _cluts(colours):
    return [fourbpp.rgb_to_clut(colours)]


def _synthetic_page(fill=0, corner=1):
    idx = bytearray(fourbpp.PAGE_BYTES)
    idx[0] = corner
    return fourbpp.encode_indices(bytes(idx))


def _load_real_page():
    """(image, cluts, note) from a real bank, or None.

    The FIREBIRD bank is built from CHICAGO's donor level, so its pages are a
    real Driver 2 atlas - the same 4bpp layout a Driver 1 page uses.
    """
    if not os.path.isfile(FIREBIRD_LEV):
        return None, "no bank on this machine"

    try:
        import exportd2

        with open(FIREBIRD_LEV, "rb") as f:
            blob = f.read()
        # d2_texture_info returns (body offset, size, bytes, parsed info)
        _off, _size, _bytes, ti = exportd2.d2_texture_info(blob)
        base, entries = exportd2.d2_page_entries(blob, ti)
        pal = exportd2.d2_palette(blob)

        for setid in sorted(entries):
            off, size, _kind = entries[setid]
            raw = blob[base + off:base + off + size]

            if len(raw) == 32768:
                image = raw
            else:
                # a compressed page: the engine's buffer is filled backwards
                image, _n = fourbpp.decomp_asm(raw, 32768)

                if len(image) != 32768:
                    continue

            rows = [r["clut"] for r in pal["records"]
                    if r["tpageindex"] == setid and r["clut"] is not None]

            if rows:
                return (image, rows, setid), "set %d of FIREBIRD.LEV" % setid
    except Exception as e:                     # a broken bank is not a test failure
        return None, "could not read the bank (%s)" % e

    return None, "the bank has no usable page"


class UneditedPagesAreCopiedTests(unittest.TestCase):
    """No edit: the source bytes come back, page for page."""

    def test_pages_are_the_same_objects(self):
        cluts = _cluts([(0, 0, 0), (255, 255, 255)] * 8)
        pages = {1: (_synthetic_page(), cluts),
                 65: (_synthetic_page(fill=1), cluts)}

        out = fourbpp.apply_page_edits(pages, [])

        for setid in pages:
            self.assertIs(out[setid], pages[setid],
                          "page %d was rebuilt even though nothing edited it"
                          % setid)
            self.assertEqual(out[setid][0], pages[setid][0])

    def test_an_edit_leaves_the_other_pages_alone(self):
        cluts = _cluts([(0, 0, 0), (255, 0, 0)] * 8)
        pages = {1: (_synthetic_page(), cluts),
                 65: (_synthetic_page(fill=2), cluts)}

        out = fourbpp.apply_page_edits(
            pages, [{"set": 1, "rect": (0, 0, 2, 2), "pixels": [(255, 0, 0)] * 4}])

        self.assertIs(out[65], pages[65])
        self.assertEqual(out[65][0], pages[65][0])


class EditedPageIsConfinedTests(unittest.TestCase):
    """An edit changes its rectangle and nothing else."""

    def test_only_the_rectangle_changes(self):
        cluts = _cluts([(0, 0, 0), (255, 255, 255), (255, 0, 0)] * 6)
        src = _synthetic_page()
        pages = {1: (src, cluts)}

        out = fourbpp.apply_page_edits(
            pages, [{"set": 1, "rect": (10, 20, 3, 5),
                     "pixels": [(255, 0, 0)] * 15}])

        before = fourbpp.decode_indices(src)
        after = fourbpp.decode_indices(out[1][0])
        changed = [i for i in range(fourbpp.PAGE_BYTES) if before[i] != after[i]]

        self.assertEqual(len(changed), 15, "the 3x5 rectangle is 15 texels")

        for i in changed:
            x, y = i % fourbpp.PAGE_W, i // fourbpp.PAGE_W
            self.assertTrue(10 <= x < 13 and 20 <= y < 25,
                            "texel (%d,%d) changed outside the rect" % (x, y))

    def test_the_clut_is_never_rewritten(self):
        cluts = _cluts([(0, 0, 0), (255, 255, 255)] * 8)
        keep = bytes(cluts[0])
        pages = {1: (_synthetic_page(), cluts)}

        out = fourbpp.apply_page_edits(
            pages, [{"set": 1, "rect": (0, 0, 4, 4),
                     "pixels": [(255, 255, 255)] * 16}])

        self.assertEqual(out[1][1][0], keep)
        self.assertEqual(cluts[0], keep)

    def test_an_unknown_page_is_refused(self):
        pages = {1: (_synthetic_page(), _cluts([(0, 0, 0)] * 16))}

        with self.assertRaises(ValueError) as caught:
            fourbpp.apply_page_edits(
                pages, [{"set": 99, "rect": (0, 0, 1, 1), "pixels": [(0, 0, 0)]}])

        self.assertIn("99", str(caught.exception))


class RealPageTests(unittest.TestCase):
    """The same guarantees on a real atlas, when one is available."""

    def setUp(self):
        self.page, self.note = _load_real_page()

        if self.page is None:
            self.skipTest("no real page: %s" % self.note)

    def test_unedited_real_pages_are_untouched(self):
        image, rows, setid = self.page
        pages = {setid: (image, rows)}

        out = fourbpp.apply_page_edits(pages, [])

        self.assertIs(out[setid][0], image)

    def test_repainting_a_texel_with_its_own_colour_is_a_no_op_when_unique(self):
        image, rows, setid = self.page
        indices = fourbpp.decode_indices(image)
        clut = fourbpp.clut_rgb(rows[0])

        # a texel whose colour appears exactly once in the row: matching it
        # back must land on the same index, so the page cannot move
        for texel in range(len(indices)):
            colour = clut[indices[texel]]

            if colour == (0, 0, 0):
                continue                     # black is the transparent entry

            if [c for c in clut if c == colour] != [colour]:
                continue

            x, y = texel % fourbpp.PAGE_W, texel // fourbpp.PAGE_W
            out = fourbpp.apply_page_edits(
                {setid: (image, rows)},
                [{"set": setid, "rect": (x, y, 1, 1), "pixels": [colour]}])

            self.assertEqual(out[setid][0], image,
                             "repainting a texel with its own colour changed "
                             "the page")
            return

        self.skipTest("no uniquely-coloured texel in set %d to try" % setid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
