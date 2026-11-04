#!/usr/bin/env python3
"""Tests for vag.py -- PSX ADPCM in both directions.

    python -m unittest test_vag -v
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vag


def _sine(samples, amplitude=12000, hz=440, rate=11025):
    return [int(amplitude * math.sin(i * 2 * math.pi * hz / rate))
            for i in range(samples)]


class RoundTripTests(unittest.TestCase):
    def test_a_sine_comes_back_close(self):
        pcm = _sine(2756)
        data, count, rms = vag.encode(pcm)

        self.assertEqual(count, len(pcm))
        self.assertEqual(len(data) % vag.BLOCK_BYTES, 0)
        self.assertLess(rms, 200, "4-bit ADPCM should be within a few percent")

        back = vag.decode(data, count)
        self.assertEqual(len(back), count)
        self.assertLess(max(abs(a - b) for a, b in zip(pcm, back)), 1024)

    def test_silence_is_exact(self):
        data, count, rms = vag.encode([0] * 560)

        self.assertEqual(rms, 0)
        self.assertEqual(set(vag.decode(data, count)), {0})

    def test_the_encoder_is_deterministic(self):
        pcm = _sine(2000)

        self.assertEqual(vag.encode(pcm)[0], vag.encode(pcm)[0],
                         "a rebuild from the same WAV must produce the same bytes")

    def test_a_short_sample_is_padded_to_whole_blocks(self):
        data, count, _rms = vag.encode([100, -100, 100])

        self.assertEqual(count, 3)
        self.assertEqual(len(data), vag.BLOCK_BYTES)

    def test_the_blocks_are_28_samples(self):
        self.assertEqual(vag.BLOCK_BYTES * 2 - 4, 28)

    def test_low_nibble_is_the_first_sample(self):
        # The engine and add_car_sounds.decode_vag read the low nibble as
        # sample 0. A high-nibble-first pack round-trips through vag.decode
        # and still plays back as garbled pairs.
        sys.path.insert(0, os.path.join(HERE, "..", "car"))
        import add_car_sounds

        pcm = _sine(56, amplitude=8000)
        data, count, _rms = vag.encode(pcm, loop=False)
        back = add_car_sounds.decode_vag(data)[:count]
        self.assertEqual(len(back), count)
        self.assertLess(max(abs(a - b) for a, b in zip(pcm, back)), 1500)


class FlagTests(unittest.TestCase):
    """The flags byte is not decoration: a stream with no end marker keeps
    decoding past its own end in the SPU, which is a host crash on PsyCross."""

    def test_a_one_shot_marks_its_last_block(self):
        data, _count, _rms = vag.encode([500] * 100, loop=False)
        blocks = [data[i * vag.BLOCK_BYTES + 1]
                  for i in range(len(data) // vag.BLOCK_BYTES)]

        self.assertEqual(blocks[0], 0x00, "a one-shot has no loop start")
        self.assertEqual(blocks[-1] & 0x01, 0x01, "the last block must end the "
                         "stream")

    def test_a_loop_marks_start_and_end(self):
        data, _count, _rms = vag.encode([500] * 100, loop=True)
        blocks = [data[i * vag.BLOCK_BYTES + 1]
                  for i in range(len(data) // vag.BLOCK_BYTES)]

        # the marker cannot be on block 0: PsyX's loop-start derivation drops a
        # point that sits at the very beginning
        self.assertEqual(blocks[0] & 0x04, 0, "no loop start on the first block")
        self.assertEqual(blocks[1] & 0x04, 0x04, "loop start on block 1")
        self.assertEqual(blocks[-1] & 0x03, 0x03, "loop end + repeat")


class RateTests(unittest.TestCase):
    def test_a_supported_rate_is_kept(self):
        self.assertEqual(vag.fit_rate(11025), (11025, None))

    def test_a_rate_outside_the_range_is_clamped_and_reported(self):
        rate, clamped = vag.fit_rate(96000)

        self.assertEqual(rate, vag.MAX_RATE)
        self.assertEqual(clamped, 96000)

    def test_resampling_keeps_the_duration(self):
        pcm = _sine(11025, rate=11025)
        out = vag.resample(pcm, 11025, 22050)

        self.assertEqual(len(out), 22050)


class RateFieldTests(unittest.TestCase):
    """`samplerate` in a bank is the recording rate in Hz.

    PsyX declares every decoded buffer to OpenAL at 44100 Hz and applies
    `AL_PITCH = attr.pitch / 4096`; the engine derives that pitch as
    `samplerate * 4096 / 44100`. The two cancel, so the played rate is the field.
    """

    def test_the_field_is_the_recording_rate(self):
        for rate in (8000, 11025, 16000, 22050):
            field = vag.rate_field(rate)

            self.assertEqual(field, rate)

            pitch = field * 4096 // 44100              # sound.c CompleteSoundSetup
            played = 44100 * pitch / 4096.0            # PsyX UpdateVoiceSample

            self.assertAlmostEqual(played, rate, delta=rate * 0.01,
                                   msg="%d Hz plays at %.0f" % (rate, played))

    def test_scaling_the_field_scales_the_pitch(self):
        # this is the failure the other reading of the code produces: 44100 in
        # the field plays a 11025 Hz sample four times too high
        pitch = 44200 * 4096 // 44100
        played = 44100 * pitch / 4096.0

        self.assertAlmostEqual(played / 11025.0, 4.0, delta=0.05)

    def test_normalise_uses_the_headroom(self):
        samples, top, gain = vag.normalise([100, -1000, 500], peak=0.9)

        self.assertEqual(top, 1000)
        self.assertAlmostEqual(gain, 0.9 * 32767 / 1000.0, places=3)
        # levelling leaves a FLOAT: the chain clamps once, in encode (see scale)
        self.assertAlmostEqual(max(abs(s) for s in samples), 0.9 * 32767,
                               places=3)

    def test_stats_read_crest_and_dc(self):
        # a limited square-ish wave: peak at full scale, rms close behind it
        hot = [32767 if i % 2 else -32767 for i in range(100)]
        st = vag.stats(hot)
        self.assertEqual(st["peak"], 32767)
        self.assertAlmostEqual(st["crest"], 1.0, places=2)
        self.assertEqual(st["clipped"], 100)
        self.assertEqual(st["longest_run"], 100)

    def test_remove_dc_centres_the_waveform(self):
        out = vag.remove_dc([1000, 1000, 1000, 1000])
        self.assertEqual(out, [0, 0, 0, 0])
        self.assertAlmostEqual(vag.stats([1000, -1000, 1000])["dc"], 333.3,
                               places=1)

    def test_level_pulls_a_hot_sample_down_to_the_rms_target(self):
        # crest 2.0 at full scale: the Firebird engine loop's problem
        hot = [32767, -32767, 16000, -16000] * 250
        out, before, gain = vag.level(hot, peak=0.999, rms=0.34, force=True)
        after = vag.stats(out)
        self.assertAlmostEqual(after["rms"] / 32767.0, 0.34, places=2)
        self.assertLess(after["peak"], 32767)
        self.assertLess(gain, 1.0)

    def test_level_never_crosses_the_peak_ceiling(self):
        # a dynamic sample: matching its rms would blow the ceiling, so the
        # ceiling wins and it lands at full scale like retail's
        dynamic = [32767] + [0] * 4999
        out, before, gain = vag.level(dynamic, peak=0.999, rms=0.34, force=True)
        self.assertLessEqual(vag.stats(out)["peak"], int(0.999 * 32767) + 1)
        self.assertAlmostEqual(gain, 0.999, places=2)

    def test_level_leaves_a_sample_inside_both_bands(self):
        # 98% peak, 33% rms, crest 3.1: retail's own measured shape - and a
        # decoded VAG, so rescaling it would cost ~45 dB of encode fidelity
        quiet, spike = 10385, 32000
        samples = ([quiet, -quiet] * 148) + [spike, -spike, spike]
        out, before, gain = vag.level(samples, peak=0.999, rms=0.34)
        self.assertEqual(gain, 1.0)
        self.assertEqual(out, samples)

    def test_level_scales_a_quiet_sample_up(self):
        quiet = [1000, -1000] * 500
        out, before, gain = vag.level(quiet, peak=0.999, rms=0.34)
        self.assertGreater(gain, 1.0)
        self.assertAlmostEqual(vag.stats(out)["rms"] / 32767.0, 0.34, places=2)

    def test_silence_is_left_alone(self):
        samples, top, gain = vag.normalise([0, 0, 0])

        self.assertEqual((samples, top, gain), ([0, 0, 0], 0, 1.0))


class NoClipTests(unittest.TestCase):
    """The chain must gain without distorting: encode is the only clamp."""

    def hot_take(self, peak=31231, n=4000):
        import math
        return [int(peak * math.sin(2 * math.pi * 148.0 * i / 16000.0))
                for i in range(n)]

    def test_a_positive_gain_does_not_clamp(self):
        hot = self.hot_take()
        for db in (3, 6, 12):
            gained = vag.gain_db(hot, db)
            self.assertGreater(max(abs(s) for s in gained), 32767,
                               "a %+d dB gain was clamped to int16" % db)
            self.assertEqual(vag.stats(gained)["clipped"], 0,
                             "a %+d dB gain pinned samples mid-chain" % db)

    def test_amplifying_then_levelling_is_identical_to_levelling(self):
        hot = self.hot_take()
        base, _b, _g = vag.level(hot, peak=0.79, rms=0.24)
        want, _n, _e = vag.encode(base, loop=True, loop_from=56)

        for db in (3, 6, 12):
            lv, _b2, _g2 = vag.level(vag.gain_db(hot, db), peak=0.79, rms=0.24)
            got, _n2, _e2 = vag.encode(lv, loop=True, loop_from=56)
            self.assertEqual(got, want,
                             "a %+d dB take baked differently after levelling" % db)

    def test_the_peak_ceiling_holds_whatever_rms_asks_for(self):
        # the loudspeaker-tone case: rms 0.9 would need a gain that blows the peak
        loud = [int(30000 * (1 if i % 2 else -1)) for i in range(2000)]
        ceiling = 0.79 * 32767

        for rms in (0.34, 0.6, 0.9):
            out, before, gain = vag.level(loud, peak=0.79, rms=rms)
            peak = max(abs(v) for v in out)
            self.assertLessEqual(peak, ceiling + 1,
                                 "rms %.2f pushed past the peak ceiling" % rms)
            self.assertEqual(vag.stats(out)["over"], 0)

    def test_no_level_leaves_a_sample_over_the_ceiling(self):
        hot = self.hot_take()
        for peak in (0.5, 0.79, 0.999):
            out, _b, _g = vag.level(hot, peak=peak, rms=0.24)
            self.assertLessEqual(max(abs(v) for v in out), peak * 32767 + 1)

    def test_encode_is_where_the_clamp_happens(self):
        loud = [40000, -40000] * 200
        data, n, _e = vag.encode(loud)
        decoded = vag.decode(data, n)
        self.assertLessEqual(max(decoded), 32767)
        self.assertGreaterEqual(min(decoded), -32768)


class LoopPointTests(unittest.TestCase):
    """find_loop exists because the SPU cannot crossfade: the join must meet."""

    def wave(self, periods=8, rate=16000, hz=148.0, phase=0.0):
        import math
        n = int(rate * periods / hz)
        return [int(20000 * math.sin(2 * math.pi * hz * i / rate + phase))
                for i in range(n)]

    def test_it_finds_a_join_much_quieter_than_the_default(self):
        # a wave that does NOT end on a zero crossing is the normal case, and
        # the default loop (block 1 to the end) then clicks every cycle
        samples = self.wave()
        found = vag.find_loop(samples, 0.0, 16000)
        self.assertIsNotNone(found)
        default = vag.seam_error(samples, 28, len(samples) - 1)
        self.assertLess(found["seam"], default)

    def test_the_loop_end_is_block_aligned(self):
        found = vag.find_loop(self.wave(), 0.0, 16000)
        self.assertEqual((found["end"] + 1) % vag.SAMPLES_PER_BLOCK, 0,
                         "the encoder can only end a loop on a block boundary")

    def test_the_loop_starts_after_the_attack(self):
        samples = self.wave(periods=80)
        found = vag.find_loop(samples, 0.1, 16000)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(found["start"], int(0.1 * 16000))

    def test_a_loop_end_trims_the_encoded_stream(self):
        samples = self.wave(periods=80)
        end = len(samples) - 100
        data, _count, _rms = vag.encode(samples, loop=True, loop_from=56,
                                        loop_end=end)
        blocks = len(data) // vag.BLOCK_BYTES
        kept = blocks * vag.SAMPLES_PER_BLOCK
        self.assertEqual(len(data) % vag.BLOCK_BYTES, 0)
        # the tail is padded up to the block boundary, never further
        self.assertLess(kept - vag.SAMPLES_PER_BLOCK, end)
        self.assertGreaterEqual(kept, end)

    def test_too_short_a_sample_has_no_loop_points(self):
        self.assertIsNone(vag.find_loop([0] * 40, 0.0, 16000))


if __name__ == "__main__":
    unittest.main(verbosity=2)
