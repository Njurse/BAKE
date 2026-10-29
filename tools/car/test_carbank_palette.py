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
PALLET = os.path.join(BANK, "pallet.bin")
PALETTE_JSON = os.path.join(BANK, "palette.json")

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


class FromPagesTests(unittest.TestCase):
    def test_the_rows_are_the_pages_own_palette(self):
        _need(self)

        import pages1

        rows = carbank_palette.rows_from_pages(BANK, {1, 2, 3})

        self.assertEqual([r["tpageindex"] for r in rows], [1, 2, 3])

        for row in rows:
            with open(os.path.join(BANK, "pages",
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
