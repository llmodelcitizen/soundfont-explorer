"""Peak safety is a publication invariant, not a gain preference (#27).

The rule, stated once: `input_tp + gain_db <= tp_ceiling_dbtp` (+ a documented tolerance). It is
enforced three times over — when the gain is computed, against the mastered artifact, and again
at manifest time — because each one can be right while another is wrong.
"""
import json
import math
import tempfile
import unittest
from pathlib import Path

from sfr.config import Paths, RenderSettings
from sfr.jobs import Job, write_meta
from sfr.loudness import (PEAK_TOLERANCE_DB, assert_peak_safe, gain_db, parse_loudnorm,
                          peak_after_gain)
from sfr.sched import JobError
from sfr.validate import validate_job
from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song

S = SETTINGS
CEIL = S.tp_ceiling_dbtp


class GainIsAlwaysSafeTests(unittest.TestCase):
    def safe(self, i, tp):
        g = gain_db(i, tp, S)
        self.assertLessEqual(peak_after_gain(tp, g), CEIL + PEAK_TOLERANCE_DB,
                             f"I={i} TP={tp} -> {g} leaves {peak_after_gain(tp, g)} dBTP")
        return g

    def test_ordinary_normalisation(self):
        self.assertAlmostEqual(self.safe(-21.3, -9.0), 5.3)     # loudness-limited, well clear

    def test_a_very_quiet_render_is_capped_by_policy_not_by_peak(self):
        g = self.safe(-60.0, -50.0)
        self.assertAlmostEqual(g, S.gain_clamp_db)              # +clamp still applies

    def test_more_attenuation_than_the_clamp_is_applied_in_full(self):
        """The defect: the old symmetric clamp handed back attenuation the ceiling required."""
        g = self.safe(-16.0, 35.0)
        self.assertAlmostEqual(g, CEIL - 35.0)                  # -36.5 dB
        self.assertLess(g, -S.gain_clamp_db)                    # deeper than the old lower clamp

    def test_the_boundary_is_inclusive(self):
        g = self.safe(-16.0, CEIL)                              # already exactly at the ceiling
        self.assertAlmostEqual(peak_after_gain(CEIL, g), CEIL)

    def test_a_hot_render_never_relies_on_the_measurement_being_sane(self):
        for bad in (float("inf"), float("-inf"), float("nan")):
            with self.assertRaises(JobError):
                gain_db(-21.3, bad, S)
            with self.assertRaises(JobError):
                gain_db(bad, -9.0, S)

    def test_every_engine_goes_through_the_same_path(self):
        """No engine-, font- or track-specific allowlist: one rule, every variant."""
        for engine_id in ENGINES_JSON["engines"]:
            for tp in (-30.0, -9.0, -1.5, 0.0, 12.0):
                g = gain_db(-21.3, tp, S)
                self.assertLessEqual(peak_after_gain(tp, g), CEIL + PEAK_TOLERANCE_DB, engine_id)


class AssertPeakSafeTests(unittest.TestCase):
    def test_passes_at_and_inside_the_ceiling(self):
        assert_peak_safe(-9.0, 5.3, S)
        assert_peak_safe(CEIL, 0.0, S)
        assert_peak_safe(CEIL, PEAK_TOLERANCE_DB, S)          # inside the documented tolerance

    def test_raises_just_outside_it(self):
        with self.assertRaises(JobError) as e:
            assert_peak_safe(CEIL, 0.2, S)
        self.assertIn("peak-unsafe", str(e.exception))

    def test_non_finite_inputs_raise_rather_than_compare(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(JobError):
                assert_peak_safe(bad, 0.0, S)
            with self.assertRaises(JobError):
                assert_peak_safe(-9.0, bad, S)


class ToleranceIsAtLeastOneMeasurementStepTests(unittest.TestCase):
    """The tolerance cannot be finer than the instrument (#44).

    ffmpeg's ebur128 reports true peak to one decimal place, so a peak is known to +/- one
    0.1 dB step. gain_db() aims a peak-limited variant at EXACTLY the ceiling, which parks it
    on the boundary — and at the old 0.01 dB, a remeasurement that rounded one step up failed
    the variant for a difference the measurement cannot resolve.
    """

    EBUR128_STEP_DB = 0.1

    def test_the_tolerance_is_not_finer_than_ebur128_can_measure(self):
        self.assertGreaterEqual(PEAK_TOLERANCE_DB, self.EBUR128_STEP_DB)

    def test_a_master_aimed_at_the_ceiling_survives_a_one_step_remeasurement(self):
        """The observed failure: input_tp -14.7 + gain 13.2 = -1.5 exactly, measured -1.4.

        It has to be a PEAK-limited variant — one the ceiling, not the loudness target, decides
        the gain for. Those are the only ones parked on the boundary, and the only ones at risk."""
        g = gain_db(-35.0, -14.7, S)                                  # loudness wants +19, peak allows +13.2
        self.assertAlmostEqual(g, 13.2)
        self.assertAlmostEqual(peak_after_gain(-14.7, g), CEIL)       # aimed at the ceiling
        assert_peak_safe(-14.7, g + self.EBUR128_STEP_DB, S)          # measured one step high

    def test_two_steps_over_is_still_refused(self):
        with self.assertRaises(JobError):
            assert_peak_safe(CEIL, 2 * self.EBUR128_STEP_DB + 0.01, S)

    def test_the_renders_27_was_written_to_catch_are_still_caught_by_miles(self):
        """A 0.1 dB tolerance does nothing for a master 27-37 dB over the ceiling."""
        for over in (5.0, 27.0, 37.0):
            with self.assertRaises(JobError):
                assert_peak_safe(CEIL, over, S)

    def test_widening_the_tolerance_did_not_change_any_gain(self):
        """Only which variants pass changes — never the audio of one that already did, which
        is why this needs no PIPELINE_VERSION bump and no corpus re-render."""
        for i, tp in ((-21.3, -9.0), (-16.0, -14.7), (-60.0, -50.0), (-16.0, 35.0)):
            g = gain_db(i, tp, S)
            self.assertEqual(g, min(min(S.lufs_target - i, CEIL - tp), S.gain_clamp_db))


class LoudnormParseTests(unittest.TestCase):
    def test_an_unparseable_value_is_a_failure_not_minus_infinity(self):
        """-inf as a true peak disables the ceiling entirely; it must never be invented."""
        blob = json.dumps({"input_i": "-21.3", "input_tp": "n/a",
                           "input_lra": "5.0", "input_thresh": "-31.0"})
        with self.assertRaises(JobError):
            parse_loudnorm("stuff " + blob + " more")

    def test_a_good_summary_still_parses(self):
        blob = json.dumps({"input_i": "-21.3", "input_tp": "-9.0",
                           "input_lra": "5.0", "input_thresh": "-31.0"})
        out = parse_loudnorm("x " + blob)
        self.assertEqual(out["input_tp"], -9.0)
        self.assertTrue(all(math.isfinite(v) for v in out.values()))


class ValidateRejectsUnsafeTests(unittest.TestCase):
    """Manifest time is the last gate before a set document: it must not trust the render."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        root = Path(self.td.name)
        self.paths = Paths(work=root / "work", out=root / "out")
        self.settings = RenderSettings(slice_s=1, listen_slice_s=1, lead_in_s=0.0, lead_out_s=0.0,
                                       sample_rate=4800, pack_size=3)
        self.job = Job(song(D=3), sf2_variant(1), self.settings,
                       ENGINES_JSON["engines"]["fluidsynth"])

    def meta(self, **over):
        m = {"status": "ok", "spec_hash": self.job.spec_hash, "master_hash": self.job.master_hash,
             "render_hash": self.job.render_hash, "lufs": -21.3, "tp": -9.0, "gain_db": 5.3,
             "master_kept": False, "song": self.job.song_id, "variant": self.job.variant_id}
        m.update(over)
        self.job.meta_path(self.paths).parent.mkdir(parents=True, exist_ok=True)
        write_meta(self.job.meta_path(self.paths), m)

    def reason(self):
        return validate_job(self.job, self.paths).reason

    def test_a_meta_whose_arithmetic_breaks_the_invariant_is_excluded(self):
        self.meta(tp=-1.0, gain_db=5.3)          # 4.3 dBTP: exactly the bug this issue is about
        self.assertEqual(self.reason(), "peak-unsafe")

    def test_an_artifact_over_the_ceiling_is_excluded_even_if_the_sum_is_fine(self):
        self.meta(output_tp=-0.2)                # the filter chain, not the arithmetic
        self.assertEqual(self.reason(), "peak-unsafe")

    def test_a_missing_or_non_finite_peak_measurement_is_excluded(self):
        self.meta(tp=None)
        self.assertEqual(self.reason(), "no-peak-measurement")
        self.meta(tp=float("nan"))
        self.assertEqual(self.reason(), "no-peak-measurement")

    def test_deep_attenuation_is_legal_now(self):
        """A render needing more cut than gain_clamp_db is correct, not out of range."""
        self.meta(tp=35.0, gain_db=-36.5, output_tp=-1.5)
        self.assertNotEqual(self.reason(), "gain-out-of-range")
        self.assertNotEqual(self.reason(), "peak-unsafe")

    def test_over_amplification_is_still_refused(self):
        self.meta(gain_db=S.gain_clamp_db + 5)
        self.assertEqual(self.reason(), "gain-out-of-range")

    def test_a_safe_render_gets_past_the_peak_gate(self):
        self.meta(output_tp=-3.7)
        self.assertNotIn(self.reason(), ("peak-unsafe", "no-peak-measurement", "gain-out-of-range"))


class PipelineIdentityTests(unittest.TestCase):
    def test_the_pipeline_version_invalidates_the_old_corpus(self):
        """Artifacts mastered under the old rule must not be reusable as cache hits (#27)."""
        from sfr import PIPELINE_VERSION
        self.assertGreaterEqual(PIPELINE_VERSION, 3)
        j = Job(song(), sf2_variant(), SETTINGS, ENGINES_JSON["engines"]["fluidsynth"])
        self.assertIn("pipeline", json.dumps(j.master_hash) + "pipeline")   # documented input
        # the hash has to actually move with the version, or nothing is invalidated
        import sfr.jobs as jobs_mod
        before = j.master_hash
        old = jobs_mod.PIPELINE_VERSION
        try:
            jobs_mod.PIPELINE_VERSION = old - 1
            self.assertNotEqual(Job(song(), sf2_variant(), SETTINGS,
                                    ENGINES_JSON["engines"]["fluidsynth"]).master_hash, before)
        finally:
            jobs_mod.PIPELINE_VERSION = old


if __name__ == "__main__":
    unittest.main()
