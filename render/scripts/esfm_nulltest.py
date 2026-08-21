# ESFMu-vs-Nuked null test (plan §6/§13 M2b). Results: docs/validation.md. Run from the repo root after rendering adl-bNN and adl-bNN-esfmu.
"""ESFMu-vs-Nuked null test (plan §6/§13): normalized cross-correlation at lag 0 over the first 60 s
of master.flac (s16 mono via ffmpeg, every 4th sample), plus the best lag within ±5 ms for context."""
import array, json, math, subprocess, sys
SECONDS, STEP = 60, 4
def mono(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-t", str(SECONDS), "-af",
                          "pan=mono|c0=0.5*c0+0.5*c1", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True).stdout
    a = array.array("h"); a.frombytes(out[: len(out) // 2 * 2]); return a
def ncc(x, y, lag=0):
    n = min(len(x), len(y)) - abs(lag)
    xs = x[max(0, -lag):max(0, -lag) + n]; ys = y[max(0, lag):max(0, lag) + n]
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for a, b in zip(xs, ys):
        a -= mx; b -= my
        sxy += a * b; sxx += a * a; syy += b * b
    return sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")
res = {}
for bank in (0, 14, 15, 58, 62, 65, 66, 67, 72, 77):
    a = mono(f"/work/renders/freedoom-e1m1/adl-b{bank}/master.flac")[::STEP]
    b = mono(f"/work/renders/freedoom-e1m1/adl-b{bank}-esfmu/master.flac")[::STEP]
    r0 = ncc(a, b)
    best = max(((ncc(a, b, lag), lag) for lag in range(-60, 61, 4)), key=lambda t: t[0])  # lags in 4-sample units = ±5 ms
    res[f"adl-b{bank}"] = {"ncc_lag0": round(r0, 4), "best_ncc": round(best[0], 4), "best_lag_ms": round(best[1] * STEP / 48, 3)}
    print(f"adl-b{bank:<3} vs -esfmu: ncc@0 = {r0:.4f}   best {best[0]:.4f} at {best[1]*STEP/48:+.2f} ms", flush=True)
json.dump(res, open("/work/m2b/nulltest.json", "w"), indent=1)
