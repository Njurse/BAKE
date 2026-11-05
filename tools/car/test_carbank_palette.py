#!/usr/bin/env python3
"""Tests for carbank_palette.py -- palette.json <-> pallet.bin <-> the pages.

The point of this tool is that an edited colour reaches the bank, so the tests
are about identity: the bodies it writes must be the ones the bake already
embeds, and an edit must change exactly one entry.

    python -m unittest test_carbank_palette -v
"""

import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import carbank_palette
import exportd2
import fourbpp

BANK = os.path.abspath(os.path.join(HERE, "..", "..", "..",
                                    "betacars", "banks", "firebird"))
LEV = os.path.join(BANK, "FIREBIRD.LEV")
# the generated palette: build/ in the split layout, the bank folder when flat
_GEN = (os.path.join(BANK, "build")
        if os.path.isfile(os.path.join(BANK, "build", "pallet.bin")) else BANK)
PALLET = os.path.join(_GEN, "pallet.bin")
PALETTE_JSON = os.path.join(_GEN, "palette.json")
# the baked pages: build/ in the split layout, the bank folder when flat
PAGES_BASE = (os.path.join(BANK, "build")
              if os.path.isdir(os.path.join(BANK, "build", "pages")) else BANK)

STP = 0x8000


def _need(test):
    for path in (LEV, PALLET, PALETTE_JSON):
        if not os.path.isfile(path):
            test.skipTest("%s is not on this machine" % os.path.basename(path))


class BodyTests(unittest.TestCase):
    def test_build_palette_body_is_what_filter_palette_writes(self):
        _need(self)

        with open(PALLET, "rb") as f:
            pal = exportd2.d2_palette_from_bytes(f.read())

        filtered, kept = exportd2.filter_palette(pal, {1})
        rows = [r for r in pal["records"]
                if r["tpageindex"] == 1 and r["clut"] is not None]

        self.assertEqual(exportd2.build_palette_body(rows, pal["total_cluts"]),
                         filtered)
        self.assertEqual(kept, len(rows))

    def test_a_body_round_trips_through_the_reader(self):
        row = bytes(struct.pack("<16H", *[0x1234] * 16))
        body = exportd2.build_palette_body(
            [{"palette": 0, "texnum": 3, "tpageindex": 7, "clut_number": -1,
              "clut": row}], 219)
        back = exportd2.d2_palette_from_bytes(body)

        self.assertEqual(back["total_cluts"], 219)
        self.assertEqual(len(back["records"]), 1)
        self.assertEqual(back["records"][0]["tpageindex"], 7)
        self.assertEqual(back["records"][0]["clut"], row)
        self.assertEqual(body[-4:], struct.pack("<i", -1), "no -1 terminator")


class JsonTests(unittest.TestCase):
    def test_an_untouched_json_reproduces_the_bank_body(self):
        _need(self)

        with open(PALETTE_JSON, "r", encoding="utf-8") as f:
            doc = json.load(f)

        row1 = carbank_palette.rows_from_doc(doc, {1})
        body = exportd2.build_palette_body(row1, doc["total_cluts"])

        with open(LEV, "rb") as f:
            blob = f.read()

        got = exportd2.d2_lump(blob, exportd2.LUMP_PALLET)
        embedded = blob[got[0]:got[0] + got[1]]
        have = carbank_palette.embedded_palette_body(LEV, {1})[0]

        self.assertEqual(have, body, "the bank's rows are not what palette.json "
                                     "describes")
        self.assertNotEqual(embedded, b"")

    def test_an_unedited_row_keeps_its_stp_bits(self):
        # rebuilding a row from rgb clears bit 15; the engine reads it
        row = bytes(struct.pack("<16H", *([0x0421 | STP] * 16)))
        doc = {"records": [{"palette": 0, "texnum": 0, "tpageindex": 1,
                            "clut": row.hex(),
                            "rgb": [list(c) for c in fourbpp.clut_rgb(row)]}]}

        got = carbank_palette.rows_from_doc(doc)[0]["clut"]

        self.assertEqual(got, row)

    def test_an_edited_row_keeps_the_transparency_it_had(self):
        row = bytes(struct.pack("<16H", *([0x0421 | STP] * 16)))
        rgb = [list(c) for c in fourbpp.clut_rgb(row)]
        rgb[3] = [255, 0, 0]

        doc = {"records": [{"palette": 0, "texnum": 0, "tpageindex": 1,
                            "clut": row.hex(), "rgb": rgb}]}

        got = carbank_palette.rows_from_doc(doc)[0]["clut"]
        words = struct.unpack("<16H", got)

        self.assertEqual(fourbpp.clut_rgb(got)[3], (248, 0, 0))
        self.assertTrue(all(w & STP for w in words),
                        "an edit must not make a semi-transparent entry opaque")


def _clut(colours):
    return bytes(fourbpp.rgb_to_clut(colours))


# Six body shades plus ten trim/glass/light entries that a recolour leaves put.
_TRIM = [(40, 40, 48), (40, 40, 48), (180, 180, 188), (180, 180, 188),
         (16, 16, 24), (16, 16, 24), (200, 32, 32), (200, 32, 32),
         (96, 112, 128), (96, 112, 128)]


def _body(shades):
    return _clut(list(shades) + list(_TRIM))


_BLUE = _body([(32, 48, 160), (48, 64, 176), (64, 80, 192),
               (80, 96, 208), (96, 112, 224), (112, 128, 240)])
_RED = _body([(160, 32, 32), (176, 48, 40), (192, 64, 48),
              (208, 80, 56), (224, 96, 64), (240, 112, 72)])
_GREEN = _body([(32, 128, 48), (40, 144, 56), (48, 160, 64),
                (56, 176, 72), (64, 192, 80), (72, 208, 88)])
_LAMP = _clut([(255, 255, 200), (255, 220, 40), (255, 180, 0), (240, 240, 240),
               (200, 200, 80), (180, 80, 20), (255, 255, 255), (80, 40, 0)] * 2)


def _page_with(rows):
    """A Driver 1 page entry: CLUT rows, then a literal 4bpp stream."""
    import pages1

    blob = struct.pack("<i", len(rows))
    for row in rows:
        blob += row
    blob += pages1.encode_literal(b"\x00" * pages1.PAGE_BYTES)
    return blob


class BodyPaletteTests(unittest.TestCase):
    """Extra D1 body CLUTs become civ_clut paint columns, not just slot 0."""

    def _emit(self, rows, paints=None):
        import os
        import tempfile

        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, "pages"))

        with open(os.path.join(folder, "pages", "PAGE_1.bin"), "wb") as handle:
            handle.write(_page_with(rows))

        notes = []
        emitted = carbank_palette.rows_from_pages(folder, {1}, paints, notes=notes)
        return emitted, notes

    def test_a_multi_clut_page_fills_paint_columns(self):
        # row 0 is the page CLUT (civ slot 0). red and green recolour it.
        # the lamp row shares nothing, and the trailing copy is not a variant.
        rows = [_BLUE, _RED, _GREEN, _LAMP, _BLUE]
        emitted, notes = self._emit(rows)
        body = [r for r in emitted if r["texnum"] == 0]

        self.assertEqual([r["palette"] for r in body], [0, 1])
        self.assertEqual(body[0]["clut"], _RED)
        self.assertEqual(body[1]["clut"], _GREEN)
        self.assertNotIn(_BLUE, [r["clut"] for r in body])
        self.assertNotIn(_LAMP, [r["clut"] for r in body])
        # the other rows are still texture ids, including the lamp
        self.assertEqual([r for r in emitted if r["texnum"] == 3][0]["clut"], _LAMP)
        self.assertTrue(notes)
        self.assertIn("civ_clut", notes[0])
        self.assertIn("cp->ap.palette", notes[0])

    def test_a_used_texture_id_is_not_consumed_as_a_paint(self):
        slots, _info = carbank_palette.body_colour_slots(
            [_BLUE, _RED], used_ids={0, 1})

        self.assertEqual(slots, {})

        slots, info = carbank_palette.body_colour_slots(
            [_BLUE, _RED], used_ids={0})

        self.assertEqual(slots[0][0], _RED)
        self.assertEqual(info[0]["kept"], 1)
        self.assertEqual(info[0]["overflow"], 0)
        self.assertEqual(info[0]["slots"], [1])

    def test_an_existing_lump_variant_keeps_its_column(self):
        # palette 0 is a real colour, not a copy of the page row, so the
        # page's extra takes the next free slot (civ column 2).
        slots, info = carbank_palette.body_colour_slots(
            [_BLUE, _RED], used_ids={0}, occupied={0: {0: _GREEN}})

        self.assertEqual(slots[0][0], _GREEN)
        self.assertEqual(slots[0][1], _RED)
        self.assertEqual(info[0]["slots"], [2])

    def test_a_duplicate_of_the_page_row_yields_slot_1(self):
        slots, _info = carbank_palette.body_colour_slots(
            [_BLUE, _RED], used_ids={0}, occupied={0: {0: _BLUE}})

        self.assertEqual(slots[0][0], _RED)
        self.assertNotIn(1, slots[0])

    def test_more_than_five_extras_are_reported_and_dropped(self):
        shades = []

        for i in range(7):
            step = i * 16
            shades.append(_body([(24 + step, 16, 16), (32 + step, 24, 16),
                                 (40 + step, 32, 16), (48 + step, 40, 16),
                                 (56 + step, 48, 16), (64 + step, 56, 16)]))

        rows = [_BLUE] + shades
        slots, info = carbank_palette.body_colour_slots(rows, used_ids={0})

        self.assertEqual(len(slots[0]), carbank_palette.PAINT_SLOTS)
        self.assertEqual(info[0]["kept"], 5)
        self.assertEqual(info[0]["overflow"], 2)
        self.assertIn("do not fit", carbank_palette.body_paint_note(1, info[0]))

    def test_body_rows_names_the_source_cluts(self):
        # the lamp is not a recolour; naming it is what puts it in a slot,
        # and it replaces auto-detection of the red and green rows.
        emitted, _notes = self._emit(
            [_BLUE, _RED, _GREEN, _LAMP],
            paints={1: {"body_rows": [0, 3]}})
        body = [r for r in emitted if r["texnum"] == 0]

        self.assertEqual([r["clut"] for r in body], [_LAMP])
        self.assertEqual(body[0]["palette"], 0)

    def test_the_d1_bake_writes_the_extra_rows_into_the_lump(self):
        import os
        import tempfile

        import bake
        import exportd2

        pallet = struct.pack("<i", 0)
        sub = struct.pack("<II", 25, len(pallet)) + pallet
        container_body = struct.pack("<I", 1) + sub
        container = struct.pack("<II", 35, len(container_body)) + container_body
        loadtime = 40
        lev = (struct.pack("<II", 37, loadtime - 8)
               + struct.pack("<8I", loadtime, len(container), 0, 0, 0, 0, 0, 0)
               + container)
        levels = tempfile.mkdtemp()

        with open(os.path.join(levels, "MIAMI.LEV"), "wb") as handle:
            handle.write(lev)

        entry = struct.pack("<i", 3) + _BLUE + _RED + _GREEN
        # geometry already carries the baked set (apply_page_plan has run)
        items = [{"geom": {"sources": [("MIAMI", 0)],
                           "polys": [{"kind": "GT3", "set": 1, "id": 0}]}}]
        blob = bake.build_pallet(
            [(1, "MIAMI", 61, entry, len(entry))],
            levels, {("MIAMI", 61): 1}, {}, items)
        got = exportd2.d2_palette_from_bytes(blob)
        body = [r for r in got["records"] if r["texnum"] == 0]

        self.assertEqual([r["palette"] for r in body], [0, 1])
        self.assertEqual(body[0]["clut"], _RED)
        self.assertEqual(body[1]["clut"], _GREEN)
        self.assertNotIn(_BLUE, [r["clut"] for r in body])

    def test_apply_leaves_a_page_with_no_variant_byte_for_byte(self):
        records = [{"palette": 0, "texnum": 0, "tpageindex": 4,
                    "clut": _BLUE},
                   {"palette": 1, "texnum": 0, "tpageindex": 4,
                    "clut": _GREEN}]
        got, notes = carbank_palette.apply_body_colour_slots(
            records, [_BLUE, _LAMP], {0}, 4)

        self.assertEqual(got, records)
        self.assertEqual(notes, [])


class FromPagesTests(unittest.TestCase):
    def test_the_rows_are_the_pages_own_palette(self):
        _need(self)

        import pages1

        rows = carbank_palette.rows_from_pages(PAGES_BASE, {1, 2, 3})
        base = [r for r in rows if r["texnum"] == 0]

        self.assertEqual([r["tpageindex"] for r in base], [1, 2, 3])

        for row in base:
            with open(os.path.join(PAGES_BASE, "pages",
                                   "PAGE_%d.bin" % row["tpageindex"]), "rb") as f:
                page = f.read()

            self.assertEqual(row["clut"], pages1.read_page(page, 0, len(page))[0][0])

    def test_the_doc_keeps_the_two_forms_in_step(self):
        row = bytes(struct.pack("<16H", *[0x7C1F] * 16))
        doc = carbank_palette.palette_doc(
            [{"palette": 0, "texnum": 0, "tpageindex": 2,
              "clut_number": -1, "clut": row}])

        self.assertEqual(doc["sets"], [2])
        self.assertEqual(bytes.fromhex(doc["records"][0]["clut"]), row)
        self.assertEqual(doc["records"][0]["rgb"][0], [248, 0, 248])


if __name__ == "__main__":
    unittest.main(verbosity=2)
