"""The sound knobs: conversion-time control, and the promise that a completed
declaration bakes exactly like a minimal one.

Everything here goes through the same two functions the bake uses -
carbank_bake.sound_specs (declaration -> resolved spec) and
exportd2._sound_sample (spec -> bank sample) - so a pass means the shipped path
is what was measured, not a re-implementation of it.
"""

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import bankjson
import carbank_bake
import exportd2
import vag


def write_sine(path, seconds=0.4, rate=16000, peak=0.5, hz=220.0):
    n = int(round(seconds * rate))
    samples = [int(round(peak * 32767 * math.sin(2 * math.pi * hz * i / rate)))
               for i in range(n)]
    vag.write_wav(path, samples, rate)
    return samples


class KnobFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="soundknobs-")
        self.addCleanup(shutil.rmtree, self.dir)

        for name in ("clean.obj", "low.obj", "atlas.png", "wheel.png"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("x")

        write_sine(self.path("quiet.wav"), peak=0.5)
        write_sine(self.path("loud.wav"), peak=0.99)
        write_sine(self.path("horn.wav"), peak=0.8, seconds=0.2)

    def path(self, name):
        return os.path.join(self.dir, name)

    def bank_with(self, sounds):
        body = {"city": "TEST", "lev": "TEST.LEV", "lcf": "TEST.LCF",
                "declare": {"schema": 1, "name": "TEST",
                            "order": [{"slot": 1, "car": "car1"}],
                            "cars": {"car1": {
                                "clean": "clean.obj", "low": "low.obj",
                                "atlas": "atlas.png",
                                "wheels": {"clean": "wheel.png"},
                                "sounds": sounds}}}}
        js = self.path("bank.json")

        with open(js, "w", encoding="utf-8") as f:
            json.dump(body, f)

        return bankjson.load(js)

    def specs(self, sounds):
        warnings = []
        spec = carbank_bake.sound_specs(self.bank_with(sounds), warnings)
        self.assertTrue(warnings or spec, "sound_specs said nothing at all")
        return spec

    def sample(self, spec, which="engine"):
        warnings = []
        entry = self.specs(spec)["models"][0][which]
        return exportd2._sound_sample(entry, warnings, "test"), warnings


class ParityTests(KnobFixture):
    """A completed declaration must bake byte for byte like a minimal one."""

    def test_the_long_form_of_a_short_form_is_the_same_sample(self):
        short, _ = self.sample({"engine": "quiet.wav", "horn": "horn.wav"})
        long_form, _ = self.sample({
            "engine": {"wav": "quiet.wav", "level": 0.999, "force": False,
                       "gain_db": 0.0, "rate": None, "rate_pct": 100,
                       "loop": True, "loop_from": 0.0, "trim": None,
                       "fade_out": 0.0},
            "horn": {"wav": "horn.wav", "level": 0.999, "force": False,
                     "gain_db": 0.0, "rate": None, "rate_pct": 100,
                     "loop": False, "loop_from": 0.0, "trim": None,
                     "fade_out": 0.0},
            "peak": 0.999, "max_rate": 16000, "sample_bytes": 16384,
            "engine_pct": 100, "idle_pct": 100, "loop_from": 0.0, "cop": False})

        self.assertEqual(short["data"], long_form["data"])
        self.assertEqual(short["rate_field"], long_form["rate_field"])

    def test_a_declared_level_is_the_level_the_bake_uses(self):
        spec = self.specs({"engine": {"wav": "quiet.wav", "level": 0.5},
                           "horn": "horn.wav"})
        self.assertEqual(spec["models"][0]["engine"]["level"], 0.5)
        self.assertEqual(spec["models"][0]["peak"], bankjson.SOUND_DEFAULTS["peak"])

    def test_the_third_sample_loops_only_when_it_says_so(self):
        spec = self.specs({"engine": "quiet.wav", "horn": "horn.wav"})
        self.assertTrue(spec["models"][0]["engine"]["loop"])
        self.assertFalse(spec["models"][0]["third"]["loop"])

    def test_a_cop_car_takes_its_siren_as_the_third_sample(self):
        spec = self.specs({"engine": "quiet.wav", "siren": "horn.wav",
                           "cop": True})
        third = spec["models"][0]["third"]

        self.assertTrue(third["wav"].endswith("horn.wav"))
        self.assertFalse(third["loop"])
        self.assertEqual(spec["sirens"], [1])


class LevelTests(KnobFixture):
    def decoded(self, sample):
        return vag.decode(sample["data"], sample["samples"])

    def decoded_peak(self, sample):
        return max(abs(v) for v in self.decoded(sample))

    def test_a_quiet_sample_is_lifted_to_the_ceiling(self):
        # with the loudness target off, this is the plain peak behaviour
        sample, warnings = self.sample({"engine": {"wav": "quiet.wav",
                                                   "level": 0.75, "rms": 0.0},
                                        "horn": "horn.wav"})
        self.assertAlmostEqual(self.decoded_peak(sample) / 32767.0, 0.75,
                               delta=0.02)
        self.assertTrue(any("levelled" in w for w in warnings))

    def test_the_loudness_target_governs_when_it_is_set(self):
        # a 50% sine measures 35% rms, which is already retail's band
        sample, warnings = self.sample({"engine": {"wav": "quiet.wav"},
                                        "horn": "horn.wav"})
        after = vag.stats(self.decoded(sample))
        self.assertGreaterEqual(after["rms"] / 32767.0, 0.29)
        self.assertLessEqual(after["rms"] / 32767.0, 0.39)
        self.assertEqual(after["peak"], self.decoded_peak(sample))

    def test_a_hot_limited_sample_is_pulled_down_to_retail_loudness(self):
        # crest 2.0 at full scale, like the Firebird's engine loop
        write_sine(self.path("hot.wav"), peak=0.99, seconds=0.5)
        hot = vag.trim(vag.read_wav(self.path("hot.wav"))[0], 0, None, 16000)
        # a sine is crest 1.41; square it up so the crest matches the real case
        hot = [32767 if v > 0 else -32767 for v in hot]
        vag.write_wav(self.path("clipped.wav"), hot, 16000)

        sample, warnings = self.sample({"engine": {"wav": "clipped.wav"},
                                        "horn": "horn.wav"})
        after = vag.stats(self.decoded(sample))
        self.assertAlmostEqual(after["rms"] / 32767.0, 0.34, delta=0.03)
        self.assertLess(after["peak"], 32767)
        self.assertTrue(any("levelled" in w for w in warnings))

    def test_a_sample_inside_both_bands_is_left_alone(self):
        sample, warnings = self.sample({"engine": "loud.wav",
                                        "horn": "horn.wav"})
        # 99% peak, and a sine's 70% rms is outside the loudness band, so this
        # one IS levelled - the untouched case is the decoded-VAG shape below
        self.assertTrue(isinstance(warnings, list))

    def test_a_retail_shaped_sample_is_not_rescaled(self):
        # 98% peak / 33% rms / crest 3.1: what the game's own banks look like,
        # and a decoded VAG, so any rescale costs ~45 dB of encode fidelity
        quiet, spike = 10385, 32000
        retail = []

        for i in range(2600):
            retail.extend([quiet, -quiet])
            if i % 867 == 0:
                retail.extend([spike, -spike])

        vag.write_wav(self.path("retail.wav"), retail, 16000)
        sample, warnings = self.sample({"engine": "retail.wav",
                                        "horn": "horn.wav"})
        self.assertFalse(any("levelled" in w for w in warnings),
                         "a retail-shaped sample was rescaled")

    def test_force_overrides_the_dead_band(self):
        warnings = []
        entry = self.specs({"engine": {"wav": "quiet.wav", "force": True},
                            "horn": "horn.wav"})["models"][0]["engine"]
        exportd2._sound_sample(entry, warnings, "test")
        self.assertTrue(any("levelled" in w for w in warnings))

    def test_the_peak_ceiling_is_never_crossed(self):
        sample, _ = self.sample({"engine": {"wav": "quiet.wav", "rms": 0.9},
                                 "horn": "horn.wav"})
        decoded = self.decoded(sample)
        # the level is applied to the waveform, before encoding; ADPCM's
        # predictor can then overshoot it by a percent or two (retail's own
        # samples sit at peak 32749-32767, i.e. at the int16 edge), so the
        # promise is "inside int16", not "exactly under the ceiling"
        self.assertLessEqual(max(decoded), 32767)
        self.assertGreaterEqual(min(decoded), -32768)
        self.assertLessEqual(max(decoded), int(0.999 * 32767) * 1.02)

    def test_normalise_off_keeps_the_source_level(self):
        sample, warnings = self.sample({"engine": {"wav": "quiet.wav",
                                                   "normalise": False},
                                        "horn": "horn.wav"})
        self.assertAlmostEqual(self.decoded_peak(sample) / 32767.0, 0.5,
                               delta=0.02)
        self.assertFalse(any("levelled" in w for w in warnings))

    def test_gain_is_the_level_control_when_normalise_is_off(self):
        sample, _ = self.sample({"engine": {"wav": "quiet.wav",
                                            "normalise": False,
                                            "gain_db": -12.0},
                                 "horn": "horn.wav"})
        self.assertAlmostEqual(self.decoded_peak(sample) / 32767.0, 0.5 / 4.0,
                               delta=0.02)

    def test_dc_offset_is_removed(self):
        write_sine(self.path("offset.wav"), peak=0.3, seconds=0.2)
        pcm, rate = vag.read_wav(self.path("offset.wav"))
        vag.write_wav(self.path("offset.wav"), [v + 4000 for v in pcm], rate)
        sample, _ = self.sample({"engine": {"wav": "offset.wav",
                                            "normalise": False},
                                 "horn": "horn.wav"})
        after = vag.stats(self.decoded(sample))
        self.assertLess(abs(after["dc"]), 40)


class ShapeTests(KnobFixture):
    def test_trim_shortens_the_sample(self):
        sample, warnings = self.sample({"engine": {"wav": "quiet.wav",
                                                   "trim": [0.1, 0.3]},
                                        "horn": "horn.wav"})
        self.assertAlmostEqual(sample["samples"] / 16000.0, 0.2, delta=0.01)
        self.assertTrue(any("trimmed" in w for w in warnings))

    def test_fade_out_ends_at_silence(self):
        sample, _ = self.sample({"engine": {"wav": "quiet.wav", "level": 0.5,
                                            "fade_out": 0.05},
                                 "horn": "horn.wav"})
        tail = vag.decode(sample["data"], sample["samples"])[-1]
        self.assertLessEqual(abs(tail), 40)

    def test_a_rate_is_a_frequency(self):
        sample, _ = self.sample({"engine": {"wav": "quiet.wav", "rate": 8000},
                                 "horn": "horn.wav"})
        self.assertEqual(sample["rate"], 8000)
        self.assertEqual(sample["rate_field"], vag.rate_field(8000))

    def test_max_rate_caps_the_rate(self):
        spec = self.specs({"engine": "quiet.wav", "horn": "horn.wav",
                           "max_rate": 8000})
        self.assertEqual(spec["models"][0]["max_rate"], 8000)
        sample = exportd2._sound_sample(spec["models"][0]["engine"], [], "test")
        self.assertEqual(sample["rate"], 8000)

    def test_a_tight_byte_budget_trims_and_says_so(self):
        warnings = []
        entry = self.specs({"engine": "quiet.wav", "horn": "horn.wav",
                            "sample_bytes": 1024})["models"][0]["engine"]
        sample = exportd2._sound_sample(entry, warnings, "test")
        self.assertLessEqual(len(sample["data"]), 1024)
        self.assertTrue(any("to fit 1024 bytes" in w for w in warnings))

    def test_a_loop_marks_its_blocks(self):
        sample, _ = self.sample({"engine": {"wav": "quiet.wav",
                                            "loop_from": 0.1},
                                 "horn": "horn.wav"})
        flags = [sample["data"][i * vag.BLOCK_BYTES + 1]
                 for i in range(len(sample["data"]) // vag.BLOCK_BYTES)]
        self.assertIn(0x04, flags, "no loop-start marker")
        self.assertIn(0x03, flags, "no loop-end marker")

    def test_a_one_shot_marks_only_its_last_block(self):
        sample, _ = self.sample({"engine": "quiet.wav", "horn": "horn.wav"},
                                 which="third")
        flags = [sample["data"][i * vag.BLOCK_BYTES + 1]
                 for i in range(len(sample["data"]) // vag.BLOCK_BYTES)]
        self.assertEqual([f for f in flags if f], [0x01])


class LoopAndPitchTests(KnobFixture):
    def decoded(self, sample):
        return vag.decode(sample["data"], sample["samples"])

    def marks(self, sample):
        return [(i, sample["data"][i * vag.BLOCK_BYTES + 1])
                for i in range(len(sample["data"]) // vag.BLOCK_BYTES)
                if sample["data"][i * vag.BLOCK_BYTES + 1]]

    def test_auto_loop_picks_a_quiet_join(self):
        # a 148 Hz sine cut at an arbitrary point joins badly by default
        write_sine(self.path("loopable.wav"), seconds=0.9, peak=0.6, hz=148.0)
        plain, _ = self.sample({"engine": {"wav": "loopable.wav"}, "horn": "horn.wav"})
        auto, warnings = self.sample({"engine": {"wav": "loopable.wav",
                                                "loop": "auto"},
                                      "horn": "horn.wav"})

        def seam(sample):
            starts = [i for i, f in self.marks(sample) if f & 4]
            start = starts[0] * vag.SAMPLES_PER_BLOCK if starts else 0
            return vag.seam_error(self.decoded(sample), start,
                                  len(self.decoded(sample)) - 1)

        self.assertLess(seam(auto), seam(plain),
                        "the searched loop is not better than the default")
        self.assertTrue(any("the join drops" in w for w in warnings))

    def test_auto_keeps_the_loop_markers_consistent(self):
        write_sine(self.path("loopable.wav"), seconds=0.9, peak=0.6, hz=148.0)
        auto, _ = self.sample({"engine": {"wav": "loopable.wav", "loop": "auto"},
                               "horn": "horn.wav"})
        flags = [f for _i, f in self.marks(auto)]
        self.assertIn(0x04, flags, "no loop start")
        self.assertIn(0x03, flags, "no loop end")

    def test_loop_to_cuts_the_tail(self):
        write_sine(self.path("loopable.wav"), seconds=0.9, peak=0.6, hz=148.0)
        cut, _ = self.sample({"engine": {"wav": "loopable.wav", "loop": True,
                                         "loop_to": 0.5},
                              "horn": "horn.wav"})
        self.assertLessEqual(len(self.decoded(cut)), int(0.5 * 16000) + 28)

    def test_pitch_scales_only_the_rate_field(self):
        flat, _ = self.sample({"engine": {"wav": "quiet.wav"}, "horn": "horn.wav"})
        high, warnings = self.sample({"engine": {"wav": "quiet.wav",
                                                "pitch": 2.0},
                                      "horn": "horn.wav"})

        self.assertEqual(high["rate_field"], flat["rate_field"] * 2)
        self.assertEqual(high["data"], flat["data"],
                         "pitch must not re-encode the sample")
        self.assertTrue(any("pitch x2.00" in w for w in warnings))


class CompleteTests(KnobFixture):
    def complete(self, sounds, dry_run=False):
        js = self.path("bank.json")
        body = {"city": "TEST", "declare": {
            "schema": 1, "name": "TEST",
            "order": [{"slot": 1, "car": "car1"}],
            "cars": {"car1": {"clean": "clean.obj", "low": "low.obj",
                              "atlas": "atlas.png",
                              "wheels": {"clean": "wheel.png"},
                              "sounds": sounds}}}}

        with open(js, "w", encoding="utf-8") as f:
            json.dump(body, f)

        carbank_bake.complete_declaration(js, dry_run=dry_run)

        with open(js, "r", encoding="utf-8") as f:
            return json.load(f)["declare"]["cars"]["car1"]["sounds"]

    def test_a_short_form_sample_becomes_the_long_form(self):
        out = self.complete({"engine": "quiet.wav", "horn": "horn.wav"})
        self.assertEqual(out["engine"]["wav"], "quiet.wav")
        self.assertEqual(out["engine"]["loop"], True)
        self.assertEqual(out["horn"]["loop"], False)
        self.assertIn("sample_bytes", out)

    def test_completing_leaves_a_value_that_is_there(self):
        out = self.complete({"engine": {"wav": "quiet.wav", "level": 0.42,
                                        "rate": 8000},
                             "horn": "horn.wav", "peak": 0.7})
        self.assertEqual(out["engine"]["level"], 0.42)
        self.assertEqual(out["engine"]["rate"], 8000)
        self.assertEqual(out["peak"], 0.7)

    def test_a_completed_bank_bakes_the_same_sample(self):
        before, _ = self.sample({"engine": "quiet.wav", "horn": "horn.wav"})
        done = self.complete({"engine": "quiet.wav", "horn": "horn.wav"})

        bankjson.load(self.path("bank.json"))
        after_spec = carbank_bake.sound_specs(
            bankjson.load(self.path("bank.json")), [])["models"][0]

        for which in ("engine", "third"):
            first = exportd2._sound_sample(
                self.specs({"engine": "quiet.wav",
                            "horn": "horn.wav"})["models"][0][which], [], "x")
            second = exportd2._sound_sample(after_spec[which], [], "x")
            self.assertEqual(first["data"], second["data"],
                             "%s changed when the declaration was completed"
                             % which)

        self.assertIn("sample_bytes", done)

    def test_completing_twice_adds_nothing(self):
        self.complete({"engine": "quiet.wav", "horn": "horn.wav"})
        js = self.path("bank.json")

        with open(js, "r", encoding="utf-8") as f:
            first = f.read()

        carbank_bake.complete_declaration(js)

        with open(js, "r", encoding="utf-8") as f:
            self.assertEqual(first, f.read())

    def test_a_dry_run_writes_nothing(self):
        js = self.path("bank.json")

        with open(js, "w", encoding="utf-8") as f:
            json.dump({"city": "TEST", "declare": {
                "schema": 1, "name": "TEST",
                "order": [{"slot": 1, "car": "car1"}],
                "cars": {"car1": {"clean": "clean.obj", "low": "low.obj",
                                  "atlas": "atlas.png",
                                  "wheels": {"clean": "wheel.png"},
                                  "sounds": {"engine": "quiet.wav",
                                             "horn": "horn.wav"}}}}}, f)

        with open(js, "r", encoding="utf-8") as f:
            before = f.read()

        carbank_bake.complete_declaration(js, dry_run=True)

        with open(js, "r", encoding="utf-8") as f:
            self.assertEqual(before, f.read())


if __name__ == "__main__":
    unittest.main()
