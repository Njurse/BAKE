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
        samples, top, gain = vag.normalise([100, -1000, 500])

        self.assertEqual(top, 1000)
        self.assertAlmostEqual(gain, 0.9 * 32767 / 1000.0, places=3)
        self.assertEqual(max(abs(s) for s in samples), int(0.9 * 32767))

    def test_silence_is_left_alone(self):
        samples, top, gain = vag.normalise([0, 0, 0])

        self.assertEqual((samples, top, gain), ([0, 0, 0], 0, 1.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
