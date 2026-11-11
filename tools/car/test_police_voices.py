"""--police-voices bakes LUMP_POLICE_VOICES from VOICES.BLK bank 1.

The slot table below is the Driver 2 phrase order (1-based slot: bank-1
sample). It is written out here so a drift in add_car_sounds.POLICE_SLOT_SOURCE
fails this file.

    python -m unittest test_police_voices -v
"""

import hashlib
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import add_car_sounds
import exportd2
import vag


# 0-based bank-1 sample, and whether the slot keeps only the first 1.34 s.
# 1:s1  2:s3  3:s2  4:s4  5:s10  6:s6  7:s7  8:s7  9:s5cut  10:s5
# 11:s5cut  12:s8  13:s9  14:s9  15:s0  16:s5cut  17:s5cut
EXPECTED_SLOTS = (
    (1, False), (3, False), (2, False), (4, False),
    (10, False), (6, False), (7, False), (7, False),
    (5, True), (5, False), (5, True),
    (8, False), (9, False), (9, False),
    (0, False), (5, True), (5, True),
)

# Sound-only patch of the fixture below, before --police-voices existed.
SOUND_ONLY_SHA256 = (
    "6370d4615d6af8d13c751b6a5af863a0629077b7da7f0bfac635a6112e738aee")


def square_vag(period, nblocks):
    """Filter 0, shift 0, one nibble held for `period` blocks then flipped."""
    out = bytearray()
    for b in range(nblocks):
        nib = 7 if ((b // period) % 2) == 0 else 8
        block = bytearray(16)
        block[0] = 0x00
        block[1] = 0x01 if b == nblocks - 1 else 0x00
        packed = (nib & 0xF) | ((nib & 0xF) << 4)
        for i in range(14):
            block[2 + i] = packed
        out += block
    return bytes(out)


def voice_bank(vags, rate=11025):
    body = struct.pack("<i", len(vags))
    addr = 0
    for data in vags:
        body += struct.pack("<IIII", addr, len(data), 0, rate)
        addr += len(data)
    return body + b"".join(vags)


def voices_blk(banks):
    off = 44
    offsets = []
    for body in banks:
        offsets.append(off)
        off += len(body)
    offsets.append(off)
    return struct.pack("<11i", *offsets) + b"".join(banks)


def minimal_lev():
    models = bytearray(4 + 13 * 12)
    struct.pack_into("<i", models, 0, 1)
    for m in range(13):
        struct.pack_into("<iii", models, 4 + m * 12,
                         0 if m == 0 else -1, -1, -1)
    seg = struct.pack("<ii", 28, len(models)) + bytes(models)
    data1 = struct.pack("<ii", 35, len(seg) + 8) + seg
    data1 += b"\x00" * ((2048 - len(data1) % 2048) % 2048)
    out = struct.pack("<ii", 37, 2048 - 8)
    out += struct.pack("<8I", 2048, len(data1), 0, 0, 0, 0, 0, 0)
    out += b"\x00" * (2048 - len(out))
    out += data1
    out += b"PAGES"
    return out


def bank1_vags():
    """11 cop-radio clips. Sample 5 is longer than 1.34 s; the rest differ."""
    vags = []
    short = 0
    for i in range(11):
        if i == 5:
            vags.append(square_vag(1, 560))
        else:
            vags.append(square_vag(1, 4 + short * 2))
            short += 1
    return vags


def fixture_bytes():
    bank0 = voice_bank([square_vag(i + 1, 2) for i in range(8)])
    banks = [bank0, voice_bank(bank1_vags())] + [b""] * 8
    return voices_blk(banks), minimal_lev()


def horns_bytes():
    """A retail horn bank whose cop-radio bank is not the one under test."""
    bank0 = voice_bank([square_vag(i + 1, 2) for i in range(8)])
    bank1 = voice_bank([square_vag(2, 4) for _ in range(11)])
    return voices_blk([bank0, bank1] + [b""] * 8)


def _slots(body):
    count = struct.unpack_from("<i", body, 0)[0]
    entries = [struct.unpack_from("<IIII", body, 4 + i * 16)
               for i in range(count)]
    payload = body[4 + count * 16:]
    return count, entries, payload


def _lump_body(blob, lump):
    got = exportd2.d2_lump(blob, lump)
    if got is None:
        return None
    off, size = got
    return blob[off:off + size]


class PoliceVoicesTests(unittest.TestCase):
    def setUp(self):
        add_car_sounds._VAG_CACHE.clear()
        self.dir = tempfile.mkdtemp(prefix="police-voices-")
        self.addCleanup(_rmtree, self.dir)
        voices, lev = fixture_bytes()
        self.voices = os.path.join(self.dir, "VOICES.BLK")
        self.horns = os.path.join(self.dir, "HORNS.BLK")
        self.sbk = os.path.join(self.dir, "SOUND.SBK")
        self.lev = os.path.join(self.dir, "CAR.LEV")
        with open(self.voices, "wb") as f:
            f.write(voices)
        with open(self.horns, "wb") as f:
            f.write(horns_bytes())
        with open(self.sbk, "wb") as f:
            f.write(voice_bank([square_vag(1, 2), square_vag(2, 2),
                                square_vag(1, 4)]))
        with open(self.lev, "wb") as f:
            f.write(lev)

    def test_bank_index_reads_bank_1(self):
        bank0 = add_car_sounds.read_d1_voices(self.voices)
        bank1 = add_car_sounds.read_d1_voices(self.voices, 1)

        self.assertEqual(len(bank0["entries"]), 8)
        self.assertEqual(len(bank1["entries"]), 11)
        self.assertNotEqual(bank0["vag"], bank1["vag"])
        addr, length, _loop, rate = bank1["entries"][5]
        pcm = vag.decode(bank1["vag"][addr:addr + length])
        self.assertGreater(len(pcm), int(round(1.34 * rate)))

    def test_flag_bakes_seventeen_slots_in_order_and_rerun_replaces(self):
        out = os.path.join(self.dir, "OUT.LEV")
        with open(self.lev, "rb") as f:
            original = f.read()
        # Beta shape: car samples from SOUND.SBK, cop lines from VOICES.BLK.
        add_car_sounds.main(
            ["--lev", self.lev, "--voices", self.sbk, "--horns", self.horns,
             "--police-voices", self.voices, "--out", out])
        with open(out, "rb") as f:
            baked = f.read()

        self.assertTrue(baked.endswith(b"PAGES"))
        self.assertEqual(_lump_body(baked, exportd2.LUMP_CAR_MODELS),
                         _lump_body(original, exportd2.LUMP_CAR_MODELS))

        types = [typ for typ, _body, _size in exportd2.d2_segments(baked)]
        self.assertEqual(types.count(exportd2.LUMP_POLICE_VOICES), 1)

        body = _lump_body(baked, exportd2.LUMP_POLICE_VOICES)
        count, entries, payload = _slots(body)
        self.assertEqual(count, exportd2.POLICE_VOICE_COUNT)
        self.assertEqual(count, len(EXPECTED_SLOTS))

        bank1 = add_car_sounds.read_d1_voices(self.voices, 1)
        sources = []
        for addr, length, _loop, rate in bank1["entries"]:
            block = bank1["vag"][addr:addr + length]
            block = block[:len(block) // 16 * 16]
            sources.append((block, int(rate)))

        # Identical phrases share one payload address. 12 distinct sources.
        # Every slot keeps the source rate. Nothing is resampled to 16000.
        unique = {}
        for i, (addr, length, loop, rate) in enumerate(entries):
            index, cut = EXPECTED_SLOTS[i]
            src, src_rate = sources[index]
            self.assertEqual(loop, 0)
            self.assertEqual(rate, src_rate)
            self.assertEqual(rate, 11025)
            self.assertEqual(addr % 16, 0)
            self.assertEqual(length % 16, 0)
            self.assertGreater(length, 0)
            self.assertLessEqual(addr + length, len(payload))
            got = payload[addr:addr + length]
            if cut:
                blocks = int(round(1.34 * src_rate / vag.SAMPLES_PER_BLOCK))
                self.assertEqual(length, blocks * 16)
                self.assertLess(length, len(src))
                self.assertEqual(got[:-16], src[:length - 16])
                self.assertEqual(got[-16], src[length - 16])
                self.assertEqual(got[-14:], src[length - 14:length])
            else:
                self.assertEqual(got, src)
            unique[addr] = length
        self.assertEqual(len(unique), 12)
        self.assertEqual(len(payload), sum(unique.values()))
        self.assertLess(len(payload), sum(length for _a, length, _l, _r
                                          in entries))

        def slot(i):
            addr, length, _loop, _rate = entries[i]
            return payload[addr:addr + length]

        self.assertEqual(slot(6), slot(7))          # s7 twice
        self.assertEqual(entries[6][0], entries[7][0])
        self.assertEqual(slot(8), slot(10))         # s5 cut, slots 9 and 11
        self.assertEqual(slot(8), slot(15))         # slots 16 and 17
        self.assertEqual(slot(8), slot(16))
        self.assertEqual(slot(12), slot(13))        # s9 twice
        self.assertNotEqual(slot(8), slot(9))       # slot 10 is the full line
        self.assertEqual(entries[8][0], entries[10][0])
        self.assertEqual(entries[8][0], entries[15][0])
        self.assertEqual(entries[8][0], entries[16][0])
        self.assertNotEqual(entries[8][0], entries[9][0])

        # Playback stops on the end flag in the VAG, not on the length field,
        # so the cut is its own stream. Its last block carries the flag; the
        # block at that distance into the full line does not.
        cut, full = slot(8), slot(9)
        self.assertGreater(len(full), len(cut))
        self.assertTrue(cut[-15] & 0x01)
        self.assertTrue(full[-15] & 0x01)
        self.assertFalse(full[len(cut) - 15] & 0x01)
        self.assertNotEqual(full[:len(cut)], cut)

        self.assertEqual(exportd2.verify_police_voices(out), [])
        print("police voices lump %d bytes" % len(body))

        types = [typ for typ, _b, _s in exportd2.d2_segments(baked)]
        self.assertEqual(types.count(exportd2.LUMP_CAR_SOUNDS), 1)

        again = os.path.join(self.dir, "AGAIN.LEV")
        add_car_sounds.main(
            ["--lev", out, "--voices", self.sbk, "--horns", self.horns,
             "--police-voices", self.voices, "--out", again])
        with open(again, "rb") as f:
            rerun = f.read()
        types = [typ for typ, _body, _size in exportd2.d2_segments(rerun)]
        self.assertEqual(types.count(exportd2.LUMP_CAR_SOUNDS), 1)
        self.assertEqual(types.count(exportd2.LUMP_POLICE_VOICES), 1)
        self.assertEqual(rerun, baked)
        self.assertEqual(_lump_body(rerun, exportd2.LUMP_POLICE_VOICES), body)
        self.assertEqual(exportd2.verify_police_voices(again), [])
        self.assertEqual(_lump_body(rerun, exportd2.LUMP_CAR_MODELS),
                         _lump_body(original, exportd2.LUMP_CAR_MODELS))

    def test_without_the_flag_output_matches_the_sound_only_patch(self):
        out = os.path.join(self.dir, "SOUNDS.LEV")
        with open(self.lev, "rb") as f:
            original = f.read()
        add_car_sounds.main(
            ["--lev", self.lev, "--voices", self.voices, "--out", out])
        with open(out, "rb") as f:
            baked = f.read()

        voices = add_car_sounds.read_d1_voices(self.voices)
        parsed = add_car_sounds.find_data1(original)
        models = add_car_sounds.present_models(original, *parsed)
        sound_body, _rows = add_car_sounds.build_sound_body(voices, models)
        expected = add_car_sounds.insert_sound_segment(original, sound_body)

        self.assertEqual(baked, expected)
        self.assertEqual(hashlib.sha256(baked).hexdigest(), SOUND_ONLY_SHA256)
        self.assertIsNone(exportd2.d2_lump(baked, exportd2.LUMP_POLICE_VOICES))
        self.assertEqual(
            [typ for typ, _b, _s in exportd2.d2_segments(baked)
             ].count(exportd2.LUMP_CAR_SOUNDS), 1)
        self.assertTrue(baked.endswith(b"PAGES"))

        twice = os.path.join(self.dir, "SOUNDS2.LEV")
        add_car_sounds.main(
            ["--lev", out, "--voices", self.voices, "--out", twice])
        with open(twice, "rb") as f:
            self.assertEqual(f.read(), baked)

    def test_the_flag_requires_its_own_voices_path(self):
        with self.assertRaises(SystemExit):
            add_car_sounds.main(
                ["--lev", self.lev, "--voices", self.voices, "--police-voices"])


def _rmtree(path):
    import shutil
    shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main(buffer=False)
