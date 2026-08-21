#!/usr/bin/env python3
"""Render the M0b-1 / M0b-4 result tables (markdown) from results.json + seam_results.json.

    python3 render/scripts/derisk/report.py work/derisk/e1m1 > work/derisk/report.md
"""
import json
import os
import sys

VARIANTS = [("fluid", "FluidSynth 2.4.4 + GeneralUser GS 1.35.sf2"), ("adl58", "libADLMIDI 1.6.2, Nuked OPL3, embedded bank 58")]


def load(root, v, name):
    with open(os.path.join(root, v, name)) as f:
        return json.load(f)


def step(r, prefix):
    return [e for e in r["steps"] if e["step"].startswith(prefix)]


def main():
    root = sys.argv[1]
    R = {v: load(root, v, "results.json") for v, _ in VARIANTS}
    S = {v: load(root, v, "seam_results.json") for v, _ in VARIANTS}
    out = []
    p = out.append

    p("### Per-engine chain: timings, loudness, sizes\n")
    p("| | " + " | ".join(l for _, l in VARIANTS) + " |")
    p("|---|" + "---|" * len(VARIANTS))
    rows = []
    def row(label, fn):
        rows.append("| %s | %s |" % (label, " | ".join(str(fn(R[v])) for v, _ in VARIANTS)))
    row("`midi_end` (last note-off, s)", lambda r: r["midi"]["last_note_s"])
    row("`D` = ceil((midi_end+3)/2)*2 (s)", lambda r: r["D_s"])
    row("raw render: length (s) / rate / fmt", lambda r: "%.2f / %s Hz / %s" % (r["raw_duration_s"], r["raw_probe"]["sample_rate"], r["raw_probe"]["sample_fmt"]))
    row("raw render: tail past last note (s)", lambda r: "%+.2f" % (r["raw_duration_s"] - r["midi"]["last_note_s"]))
    row("render CPU s / wall s", lambda r: "%.2f / %.2f" % (step(r, "render")[0]["cpu_s"], step(r, "render")[0]["wall_s"]))
    row("loudnorm measure (raw) CPU s", lambda r: "%.2f" % step(r, "loudnorm measure raw")[0]["cpu_s"])
    row("raw I (LUFS) / TP (dBTP) / LRA", lambda r: "%s / %s / %s" % (r["loudnorm_raw"]["input_i"], r["loudnorm_raw"]["input_tp"], r["loudnorm_raw"]["input_lra"]))
    row("gain_db = min(-16-I, -1.5-TP), clamp +-30", lambda r: "%+.2f (limited by %s)" % (r["gain_db"], r["gain_limited_by"]))
    row("master re-measured I / TP", lambda r: "%s / %s" % (r["loudnorm_master"]["input_i"], r["loudnorm_master"]["input_tp"]))
    row("master FLAC: samples / bytes", lambda r: "%s (= %d x 48000) / %s (%.1f MB)" % (r["master_probe"]["duration_ts"], r["D_s"], "{:,}".format(r["master_bytes"]), r["master_bytes"] / 1e6))
    row("master encode CPU s", lambda r: "%.2f" % step(r, "master")[0]["cpu_s"])
    row("padded s16le stream samples (expected D*48000+6720)", lambda r: "%d (%s)" % (r["pad_samples"], "ok" if r["pad_samples"] == r["pad_samples_expected"] else "MISMATCH"))
    row("opusenc x14 CPU s (10 scrub + 2 listen + 2 extra)", lambda r: "%.2f" % r["totals_by_tool"]["opusenc"]["cpu_s"])
    row("opusenc monolithic 190 s @48k CPU s", lambda r: "%.2f" % step(r, "opusenc monolithic")[0]["cpu_s"])
    row("OpusHead pre-skip (samples)", lambda r: r["opus_pre_skip"])
    row("scrub seg 0: default vs `--padding 0` (bytes)", lambda r: "%d vs %d (-%d)" % (r["scrub"][0]["bytes"], r["seg0_padding0_bytes"], r["scrub"][0]["bytes"] - r["seg0_padding0_bytes"]))
    row("whole-chain CPU s / wall s (incl. verification)", lambda r: "%.1f / %.1f" % (r["total_cpu_s"], r["total_wall_s"]))
    out.extend(rows)
    p("")

    p("### Segment sizes and opusdec sample counts\n")
    p("| segment | padded start sample | master span (s) | " + " | ".join("%s KB / decoded" % v for v, _ in VARIANTS) + " |")
    p("|---|---|---|" + "---|" * len(VARIANTS))
    for i in range(len(R["fluid"]["scrub"])):
        cells = []
        for v, _ in VARIANTS:
            s = R[v]["scrub"][i]
            cells.append("%.1f / %d %s" % (s["kb"], s["decoded_samples"], "ok" if s["exact"] else "BAD"))
        s = R["fluid"]["scrub"][i]
        p("| scrub %04d @48 kbps | %d | [%.2f, %.2f] | %s |" % (i, s["pad_start_sample"], 2 * i - 0.12, 2 * i + 2.02, " | ".join(cells)))
    for i in range(len(R["fluid"]["listen"])):
        cells = []
        for v, _ in VARIANTS:
            s = R[v]["listen"][i]
            cells.append("%.1f / %d %s" % (s["kb"], s["decoded_samples"], "ok" if s["exact"] else "BAD"))
        s = R["fluid"]["listen"][i]
        p("| listen %03d @96 kbps | %d | [%.2f, %.2f] | %s |" % (i, s["pad_start_sample"], 10 * i - 0.12, 10 * i + 10.02, " | ".join(cells)))
    for v, _ in VARIANTS:
        sc = [s["bytes"] for s in R[v]["scrub"]]
        p("")
        p("- %s: scrub mean %.1f KB (min %.1f, max %.1f) -> a 24-member pack ~%.0f KB; listen mean %.1f KB; monolithic 190 s @48 kbps = %.0f KB (%.1f KB per 2 s, i.e. segment overhead incl. 7%% lead-in/out and Ogg/OpusHead/OpusTags ~%.0f%%)." % (
            v, sum(sc) / len(sc) / 1024, min(sc) / 1024, max(sc) / 1024, 24 * sum(sc) / len(sc) / 1024,
            sum(s["bytes"] for s in R[v]["listen"]) / len(R[v]["listen"]) / 1024, R[v]["mono_bytes"] / 1024,
            R[v]["mono_bytes"] / 95 / 1024, 100 * (sum(sc) / len(sc) / (R[v]["mono_bytes"] / 95) - 1)))
    p("")

    p("### Seam test (5 ms linear crossfade, 10 ms analysis window centred on each seam)\n")
    for v, label in VARIANTS:
        s = S[v]
        p("**%s** (%s) - seg 0 decode vs master best lag: %d samples; overlap %d samples; crossfade %d samples.\n" % (v, label, s["seg0_best_lag_vs_master"], s["overlap_samples"], s["xfade_samples"]))
        p("| seam | t (s) | xfade NSR | butt NSR | seg k alone | seg k+1 alone | xfade - worst single | mid-slice NSR | whole-slice NSR | xfade vs mono NSR | inter-seg diff (dBFS) | butt jump at S (dBFS) | xfade-added step (dBFS) | music max step (dBFS) | stitched max step (dBFS) |")
        p("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for e in s["seams"]:
            p("| %d|%d | %d | %.2f | %.2f | %.2f | %.2f | %+.2f | %.2f | %.2f | %.2f | %.1f | %.1f | %.1f | %.1f | %.1f |" % (
                e["k"], e["k"] + 1, e["seam_master_s"], e["xfade"]["vs_master"]["nsr_db"], e["butt"]["vs_master"]["nsr_db"],
                e["segk_alone"]["vs_master"]["nsr_db"], e["segk1_alone"]["vs_master"]["nsr_db"], e["xfade_nsr_minus_worst_single_db"],
                e["mid_slice_k1"]["vs_master"]["nsr_db"], e["whole_slice_k1_vs_master"]["nsr_db"], e["xfade"]["vs_mono"]["nsr_db"],
                e["diff_power_dbfs"], e["butt_jump_dbfs"], e["xfade_added_step_dbfs"], e["music_max_step_dbfs"], e["stitched_max_step_dbfs"]))
        w = s["worst"]
        p("")
        p("- worst seam: xfade NSR minus worst single-segment NSR **%+.2f dB** (limit +3), minus best single %+.2f dB; minus mid-slice window %+.2f dB (content-dependent, see text)." % (w["xfade_nsr_minus_worst_single_db"], w["xfade_nsr_minus_best_single_db"], w["xfade_nsr_minus_mid_db"]))
        p("- largest crossfade-added per-sample step **%.1f dBFS** (limit -40); largest butt-join jump %.1f dBFS; stitched max step exceeds the music's own max step by at most %+.2f dB (butt: %+.2f dB) - but so does a single un-seamed segment (%+.2f dB): stitched minus single-segment max step is at most %+.2f dB." % (w["xfade_added_step_dbfs"], w["butt_jump_dbfs"], w["xfade_step_minus_music_step_db"], w["butt_step_minus_music_step_db"], w["single_step_minus_music_step_db"], w["xfade_step_minus_single_step_db"]))
        p("- edge effects: seg k+1 (120 ms into a fresh encode) minus seg k (1.88 s in) in the same window: mean %+.2f dB; first 10 ms of seg k+1 minus seg k in the same window: mean %+.2f dB, max %+.2f dB." % (w["mean_edge_penalty_k1_minus_k_db"], w["mean_coldstart_penalty_db"], w["max_coldstart_penalty_db"]))
        p("- vs monolithic decode instead of master: mean %+.2f dB relative to vs-master." % w["mean_vs_mono_minus_vs_master_db"])
        p("- verdict: energy %s, step %s -> **%s**" % ("pass" if s["pass_energy"] else "FAIL", "pass" if s["pass_step"] else "FAIL", "PASS" if s["pass"] else "FAIL"))
        p("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
