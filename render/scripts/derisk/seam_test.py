#!/usr/bin/env python3
"""M0b item 4 — seam test for the scrub tier (stdlib only; runs INSIDE sfr-render: needs ffmpeg + opusdec).

    python3 /scripts/seam_test.py /derisk/e1m1/fluid [--xfade-ms 5] [--window-ms 10] [--json out.json]

Timeline: "padded" stream = master with 120 ms lead-in (5760 samples) and 20 ms lead-out; scrub
segment k = padded[k*96000 : k*96000+102720]; the nominal seam between slice k and k+1 sits at
master time 2(k+1) s = padded sample S = (k+1)*96000 + 5760.  Consecutive segments overlap on
padded [S-5760, S+960) = 6720 samples of bit-identical master audio.  The client plays seg k up to
the seam and seg k+1 from the seam with a linear crossfade of `xfade` ms centred on S.

For every seam k|k+1, in a `window` ms window centred on S, we compute against reference (A) the
padded master (bit-identical to the bytes opusenc consumed) and (B) the decoded monolithic 48 kbps
encode of the whole padded stream:
  * NSR (error/signal, dB) and error power (dBFS) of: the crossfaded stitch, the butt-join (hard
    switch at S), seg k ALONE and seg k+1 ALONE in that same window (the proper controls: same
    music, same codec, no seam), a 10 ms window at the centre of slice k+1 and the whole 2 s slice;
  * the inter-segment difference d = seg_k - seg_k+1 over the window ("switching noise"): its power,
    the instantaneous jump |d[S]| a butt-join would add, and the per-sample step a linear crossfade
    adds (max|d|/L), compared with the music's own largest sample-to-sample step in the window;
  * the cold-start penalty: NSR of the first 10 ms of seg k+1 (padded (k+1)*96000 ..) vs seg k in the
    same window (seg k is 1.88 s into its stream there) — what the 120 ms lead-in exists to hide.
Acceptance (plan M0b-4, restated on like-for-like windows): stitched NSR <= max(seg k alone,
seg k+1 alone) + 3 dB at every seam, and the crossfade-added step < -40 dBFS and below the music's
own steps.
"""
import argparse
import array
import json
import math
import os
import subprocess
import sys

SR = 48000
CH = 2
LEAD_IN = 5760
HOP = 96000
SEG = 102720


def db(x):
    return 10.0 * math.log10(x) if x > 0 else float("-inf")


def dba(x):
    return 20.0 * math.log10(x) if x > 0 else float("-inf")


def r2(x):
    return round(x, 2) if isinstance(x, float) and math.isfinite(x) else x


def read_f32(path):
    a = array.array("f")
    with open(path, "rb") as f:
        a.frombytes(f.read())
    return a


def read_s16_as_f32(path, start, n):
    a = array.array("h")
    with open(path, "rb") as f:
        f.seek(start * 2 * CH)
        a.frombytes(f.read(n * 2 * CH))
    return array.array("f", [v / 32768.0 for v in a])


def sh(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        sys.exit("FAILED: %s\n%s" % (" ".join(cmd), p.stderr.decode(errors="replace")[-2000:]))


def sub(a, lo, hi):
    return array.array("f", a[lo * CH:hi * CH])


def nsr(sig, ref):
    e = s = 0.0
    for i in range(len(ref)):
        d = sig[i] - ref[i]
        e += d * d
        s += ref[i] * ref[i]
    n = len(ref)
    return {"nsr_db": r2(db(e / s)) if s > 0 else None, "err_dbfs": r2(db(e / n)), "sig_dbfs": r2(db(s / n))}


def max_step(sig):
    m = 0.0
    for i in range(CH, len(sig)):
        m = max(m, abs(sig[i] - sig[i - CH]))
    return m


def best_lag(sig, ref, lo, n, maxlag):
    best = (None, -1e9)
    for lag in range(-maxlag, maxlag + 1):
        acc = 0.0
        for i in range(lo, lo + n):
            acc += sig[(i + lag) * CH] * ref[i * CH]
        if acc > best[1]:
            best = (lag, acc)
    return best[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vdir")
    ap.add_argument("--xfade-ms", type=float, default=5.0)
    ap.add_argument("--window-ms", type=float, default=10.0)
    ap.add_argument("--json")
    a = ap.parse_args()
    v = a.vdir
    tmp = os.path.join(v, "seamtmp")
    os.makedirs(tmp, exist_ok=True)

    pad = os.path.join(tmp, "pad.s16")
    if not os.path.exists(pad):
        sh(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", os.path.join(v, "master.flac"),
            "-af", "adelay=120|120,apad=pad_dur=0.02", "-f", "s16le", "-ac", "2", "-ar", "48000", pad])
    mono = os.path.join(tmp, "mono.f32")
    if not os.path.exists(mono):
        sh(["opusdec", "--quiet", "--rate", "48000", "--float", os.path.join(v, "mono.opus"), mono])
    mono_all = read_f32(mono)
    segs = sorted(f for f in os.listdir(os.path.join(v, "seg")) if f.endswith(".opus"))
    dec = []
    for f in segs:
        out = os.path.join(tmp, f.replace(".opus", ".f32"))
        if not os.path.exists(out):
            sh(["opusdec", "--quiet", "--rate", "48000", "--float", os.path.join(v, "seg", f), out])
        d = read_f32(out)
        assert len(d) == SEG * CH, (f, len(d))
        dec.append(d)

    L = int(round(a.xfade_ms * SR / 1000))
    W = int(round(a.window_ms * SR / 1000))
    res = {"variant": os.path.basename(v.rstrip("/")), "xfade_ms": a.xfade_ms, "window_ms": a.window_ms,
           "xfade_samples": L, "overlap_samples": SEG - HOP, "segments": len(dec), "seams": []}
    res["seg0_best_lag_vs_master"] = best_lag(dec[0], read_s16_as_f32(pad, 0, SEG), 20000, 4800, 400)

    for k in range(len(dec) - 1):
        S = (k + 1) * HOP + LEAD_IN
        lo, hi = S - W // 2, S + W // 2
        offA, offB = k * HOP, (k + 1) * HOP
        A, B = dec[k], dec[k + 1]
        segA, segB = sub(A, lo - offA, hi - offA), sub(B, lo - offB, hi - offB)
        n = hi - lo
        xf = array.array("f", bytes(4 * CH * n))
        butt = array.array("f", bytes(4 * CH * n))
        diff = array.array("f", bytes(4 * CH * n))
        for i in range(n):
            t = (lo + i) - (S - L // 2)
            w = 0.0 if t < 0 else 1.0 if t >= L else (t + 0.5) / L
            for c in range(CH):
                sa, sb = segA[i * CH + c], segB[i * CH + c]
                xf[i * CH + c] = (1.0 - w) * sa + w * sb
                butt[i * CH + c] = sb if lo + i >= S else sa
                diff[i * CH + c] = sa - sb
        refA = read_s16_as_f32(pad, lo, n)
        refB = sub(mono_all, lo, hi)
        # controls
        M = S + HOP // 2
        mid = sub(B, M - W // 2 - offB, M + W // 2 - offB)
        midA = read_s16_as_f32(pad, M - W // 2, W)
        midB = sub(mono_all, M - W // 2, M + W // 2)
        slice_nsr = nsr(sub(B, S - offB, S + HOP - offB), read_s16_as_f32(pad, S, HOP))
        # cold start: first W of seg k+1 vs seg k at the same place
        hlo, hhi = offB, offB + W
        headB = sub(B, 0, W)
        headA = sub(A, hlo - offA, hhi - offA)
        headRef = read_s16_as_f32(pad, hlo, W)
        # steps
        dS = max(abs(diff[(S - lo) * CH + c]) for c in range(CH))
        dmax = max(abs(x) for x in diff)
        seam = {
            "k": k, "seam_master_s": 2 * (k + 1),
            "xfade": {"vs_master": nsr(xf, refA), "vs_mono": nsr(xf, refB)},
            "butt": {"vs_master": nsr(butt, refA), "vs_mono": nsr(butt, refB)},
            "segk_alone": {"vs_master": nsr(segA, refA), "vs_mono": nsr(segA, refB)},
            "segk1_alone": {"vs_master": nsr(segB, refA), "vs_mono": nsr(segB, refB)},
            "mid_slice_k1": {"vs_master": nsr(mid, midA), "vs_mono": nsr(mid, midB)},
            "whole_slice_k1_vs_master": slice_nsr,
            "coldstart_head_k1_vs_master": nsr(headB, headRef),
            "coldstart_segk_same_window_vs_master": nsr(headA, headRef),
            "diff_power_dbfs": r2(db(sum(x * x for x in diff) / len(diff))),
            "butt_jump_dbfs": r2(dba(dS)),
            "xfade_added_step_dbfs": r2(dba(dmax / L)),
            "max_diff_dbfs": r2(dba(dmax)),
            "music_max_step_dbfs": r2(dba(max_step(refA))),
            "music_step_at_seam_dbfs": r2(dba(max(abs(refA[(S - lo) * CH + c] - refA[(S - lo - 1) * CH + c]) for c in range(CH)))),
            "stitched_max_step_dbfs": r2(dba(max_step(xf))),
            "butt_max_step_dbfs": r2(dba(max_step(butt))),
            "segk_alone_max_step_dbfs": r2(dba(max_step(segA))),
            "segk1_alone_max_step_dbfs": r2(dba(max_step(segB))),
        }
        x, ka, kb = seam["xfade"]["vs_master"]["nsr_db"], seam["segk_alone"]["vs_master"]["nsr_db"], seam["segk1_alone"]["vs_master"]["nsr_db"]
        seam["xfade_nsr_minus_best_single_db"] = r2(x - min(ka, kb))
        seam["xfade_nsr_minus_worst_single_db"] = r2(x - max(ka, kb))
        seam["xfade_nsr_minus_mid_db"] = r2(x - seam["mid_slice_k1"]["vs_master"]["nsr_db"])
        seam["coldstart_penalty_db"] = r2(seam["coldstart_head_k1_vs_master"]["nsr_db"] - seam["coldstart_segk_same_window_vs_master"]["nsr_db"])
        seam["edge_penalty_k1_minus_k_db"] = r2(kb - ka)
        res["seams"].append(seam)

    ss = res["seams"]
    worst = {
        "xfade_nsr_minus_worst_single_db": max(s["xfade_nsr_minus_worst_single_db"] for s in ss),
        "xfade_nsr_minus_best_single_db": max(s["xfade_nsr_minus_best_single_db"] for s in ss),
        "xfade_nsr_minus_mid_db": max(s["xfade_nsr_minus_mid_db"] for s in ss),
        "xfade_added_step_dbfs": max(s["xfade_added_step_dbfs"] for s in ss),
        "butt_jump_dbfs": max(s["butt_jump_dbfs"] for s in ss),
        "xfade_step_minus_music_step_db": max(s["stitched_max_step_dbfs"] - s["music_max_step_dbfs"] for s in ss),
        "butt_step_minus_music_step_db": max(s["butt_max_step_dbfs"] - s["music_max_step_dbfs"] for s in ss),
        "single_step_minus_music_step_db": max(max(s["segk_alone_max_step_dbfs"], s["segk1_alone_max_step_dbfs"]) - s["music_max_step_dbfs"] for s in ss),
        "xfade_step_minus_single_step_db": max(s["stitched_max_step_dbfs"] - max(s["segk_alone_max_step_dbfs"], s["segk1_alone_max_step_dbfs"]) for s in ss),
        "mean_edge_penalty_k1_minus_k_db": sum(s["edge_penalty_k1_minus_k_db"] for s in ss) / len(ss),
        "mean_coldstart_penalty_db": sum(s["coldstart_penalty_db"] for s in ss) / len(ss),
        "max_coldstart_penalty_db": max(s["coldstart_penalty_db"] for s in ss),
        "mean_vs_mono_minus_vs_master_db": sum(s["xfade"]["vs_mono"]["nsr_db"] - s["xfade"]["vs_master"]["nsr_db"] for s in ss) / len(ss),
    }
    res["worst"] = {k: r2(v) for k, v in worst.items()}
    res["pass_energy"] = worst["xfade_nsr_minus_worst_single_db"] <= 3.0
    res["pass_step"] = worst["xfade_added_step_dbfs"] <= -40.0
    res["pass"] = res["pass_energy"] and res["pass_step"]

    print("variant %s  xfade %.1f ms (%d smp)  window %.1f ms  seg0 lag vs master: %d" % (res["variant"], a.xfade_ms, L, a.window_ms, res["seg0_best_lag_vs_master"]))
    hdr = ("seam", "t(s)", "xf", "butt", "segk", "segk+1", "mid", "slice", "mono", "diffP", "buttJmp", "xfStep", "musStep", "xfMax", "buttMax", "cold", "warm")
    print("%4s %5s | %7s %7s %7s %7s %7s %7s %7s | %7s %7s %7s %7s %7s %7s | %7s %7s" % hdr)
    for s in ss:
        print("%4d %5.1f | %7.2f %7.2f %7.2f %7.2f %7.2f %7.2f %7.2f | %7.2f %7.2f %7.2f %7.2f %7.2f %7.2f | %7.2f %7.2f" % (
            s["k"], s["seam_master_s"], s["xfade"]["vs_master"]["nsr_db"], s["butt"]["vs_master"]["nsr_db"],
            s["segk_alone"]["vs_master"]["nsr_db"], s["segk1_alone"]["vs_master"]["nsr_db"],
            s["mid_slice_k1"]["vs_master"]["nsr_db"], s["whole_slice_k1_vs_master"]["nsr_db"], s["xfade"]["vs_mono"]["nsr_db"],
            s["diff_power_dbfs"], s["butt_jump_dbfs"], s["xfade_added_step_dbfs"], s["music_max_step_dbfs"],
            s["stitched_max_step_dbfs"], s["butt_max_step_dbfs"],
            s["coldstart_head_k1_vs_master"]["nsr_db"], s["coldstart_segk_same_window_vs_master"]["nsr_db"]))
    print("worst:", json.dumps(res["worst"]), "PASS" if res["pass"] else "FAIL")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
