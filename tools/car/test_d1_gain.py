"""Driver 1 import loudness: match a stock Driver 2 car bank, after encode.

    python -m unittest test_d1_gain -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import add_car_sounds
import vag


def _voices(data, rate=11025):
    return {"entries": [(0, len(data), 1, rate)], "vag": data, "kind": "voices"}


class D1GainTests(unittest.TestCase):
    def setUp(self):
        add_car_sounds._VAG_CACHE.clear()

    def test_a_limited_loop_is_pulled_down_to_retail_rms(self):
        # crest ~1 at full scale: the old 0.45 peak scale left this at 45% rms,
        # about +2.4 dB on retail's 34%, and it sounded crushed-loud.
        pcm = [32767 if (i // 14) % 2 == 0 else -32767 for i in range(28 * 6)]
        data, _count, _err = vag.encode(pcm, loop=True)
        out = add_car_sounds._scaled_vag(_voices(data), 0, 1.0)
        back = vag.stats(add_car_sounds.decode_vag(out)[:len(pcm)])

        self.assertAlmostEqual(back["rms"] / 32767.0, vag.DEFAULT_RMS, delta=0.05)
        self.assertLess(back["rms"] / 32767.0, 0.42)
        # loop flags survive the re-encode
        self.assertTrue(any(out[b * 16 + 1] & 0x01
                            for b in range(len(out) // 16)))

    def test_a_dynamic_loop_is_not_left_at_a_45_percent_peak(self):
        # crest ~4. Peak-scaling to 0.45 leaves rms near 11%, well under retail.
        quiet, spike = 4000, 16000
        pcm = []

        for i in range(28 * 8):
            pcm.append(spike if i % 80 == 0 else (quiet if (i & 1) == 0 else -quiet))

        data, _count, _err = vag.encode(pcm, loop=True)
        out = add_car_sounds._scaled_vag(_voices(data), 0, 1.0)
        back = vag.stats(add_car_sounds.decode_vag(out)[:len(pcm)])
        # the peak ceiling stops a crest-4 loop short of 34% rms; it must
        # still land well above the old 0.45-peak result (~11% rms)
        self.assertGreater(back["rms"] / 32767.0, 0.18)
        self.assertLessEqual(back["peak"], 32767)

    def test_a_sample_already_at_retail_loudness_is_not_reencoded(self):
        quiet, spike = 9000, 28000
        pcm = []

        for i in range(28 * 10):
            pcm.append(spike if i % 90 == 0 else (quiet if (i & 1) == 0 else -quiet))

        levelled, _before, _gain = vag.level(
            pcm, peak=vag.DEFAULT_PEAK, rms=vag.DEFAULT_RMS, force=True)
        data, count, _err = vag.encode_settled(levelled, loop=True)
        decoded = vag.decode(data, len(levelled))
        _out, _before2, applied = vag.level(
            decoded, peak=vag.DEFAULT_PEAK, rms=vag.DEFAULT_RMS)

        self.assertEqual(applied, 1.0, "fixture was not a fixed point of level()")
        again = add_car_sounds._scaled_vag(_voices(data), 0, 1.0)
        self.assertEqual(again, data)


if __name__ == "__main__":
    unittest.main()
