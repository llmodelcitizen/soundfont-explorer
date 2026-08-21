#!/usr/bin/env python3
"""M0b item 1 — full render chain for ONE variant (runs INSIDE the sfr-render container).

    python3 /scripts/chain.py --out /derisk/e1m1/fluid --midi /derisk/e1m1.mid \
        --engine fluid --font "/fonts/GeneralUser GS 1.35.sf2"
    python3 /scripts/chain.py --out /derisk/e1m1/adl58 --midi /derisk/e1m1.mid \
        --engine adl --bank 58

Steps (plan §8.3): render -> loudnorm measure -> linear gain -> soxr 48 k FLAC master (exactly D s)
-> padded s16le stream (120 ms lead-in / 20 ms lead-out) -> 10 scrub + 2 listen Opus segments
-> opusdec sample-count verification -> monolithic 48 kbps encode (seam-test reference).
Writes <out>/results.json with timings (wall + child CPU), loudness, sizes and every command run.
Stdlib only.
"""
import argparse
import json
import math
import os
import resource
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from midi_end import parse as midi_parse  # noqa: E402

SR = 48000
CH = 2
LEAD_IN = 5760          # 0.12 s
LEAD_OUT = 960          # 0.02 s
SCRUB_HOP = 96000       # 2 s
SCRUB_SAMPLES = 102720  # 2.14 s
LISTEN_HOP = 480000     # 10 s
LISTEN_SAMPLES = 486720  # 10.14 s
N_SCRUB = 10
N_LISTEN = 2

LOG = []


def cpu_children():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def run(name, cmd, cwd=None, stdin_bytes=None, capture=True):
    """Run a command, record wall + CPU-seconds of the child, return CompletedProcess."""
    c0, t0 = cpu_children(), time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, input=stdin_bytes,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    wall, cpu = time.monotonic() - t0, cpu_children() - c0
    entry = {"step": name, "cmd": " ".join(shlex.quote(c) for c in cmd),
             "cwd": cwd, "wall_s": round(wall, 3), "cpu_s": round(cpu, 3), "rc": p.returncode}
    LOG.append(entry)
    if p.returncode != 0:
        sys.stderr.write("FAILED: %s\n%s\n" % (entry["cmd"], (p.stderr or b"").decode(errors="replace")[-4000:]))
        sys.exit(1)
    return p


def ffprobe(path):
    p = run("ffprobe " + os.path.basename(path),
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name,sample_rate,channels,sample_fmt,duration,duration_ts,bits_per_raw_sample",
             "-of", "json", path])
    return json.loads(p.stdout)["streams"][0]


def loudnorm_measure(path):
    p = run("loudnorm measure " + os.path.basename(path),
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
             "-af", "loudnorm=I=-16:TP=-1.5:print_format=json", "-f", "null", "-"])
    txt = p.stderr.decode(errors="replace")
    j = txt[txt.rfind("{"):txt.rfind("}") + 1]
    return json.loads(j)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--midi", required=True)
    ap.add_argument("--engine", choices=["fluid", "adl"], required=True)
    ap.add_argument("--font")
    ap.add_argument("--bank", type=int)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()

    out = a.out
    for d in ("seg", "listen", "dec"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    res = {"engine": a.engine, "midi": a.midi, "out": out,
           "hostname": os.uname().nodename, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # --- D from the MIDI ---------------------------------------------------------------
    mi = midi_parse(a.midi)
    D = mi["D_s"]
    N = D // 2
    res["midi"] = mi
    res["D_s"] = D
    res["N_slices"] = N

    # --- 1. render ----------------------------------------------------------------------
    raw = os.path.join(out, "raw.wav")
    if a.engine == "fluid":
        res["font"] = a.font
        res["font_bytes"] = os.path.getsize(a.font)
        cmd = ["fluidsynth", "-ni", "-F", raw, "-T", "wav", "-O", "float", "-r", "48000",
               "-g", "0.5", "-R", "0", "-C", "0", "-o", "synth.polyphony=256", "-o", "synth.cpu-cores=1"]
        if res["font_bytes"] >= 128 * 2**20:
            cmd += ["-o", "synth.dynamic-sample-loading=1"]
        cmd += [a.font, a.midi]
        run("render fluidsynth", cmd)
    else:
        res["bank"] = a.bank
        link = os.path.join(out, "e1m1.mid")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(a.midi, link)
        # WAVE_ONLY build: no -w/-nl; always writes "<input>.wav" = e1m1.mid.wav (name appended, 44100 Hz) next to the input.
        run("render adlmidiplay", ["adlmidiplay", "e1m1.mid", "-f32", "-vm", "0", "--gain", "2.0",
                                    "--emu-nuked", str(a.bank), "1"], cwd=out)
        os.replace(os.path.join(out, "e1m1.mid.wav"), raw)
        os.unlink(link)
    res["raw_probe"] = ffprobe(raw)
    res["raw_bytes"] = os.path.getsize(raw)
    res["raw_duration_s"] = float(res["raw_probe"]["duration"])

    # --- 2. measure --------------------------------------------------------------------
    m = loudnorm_measure(raw)
    I, TP = float(m["input_i"]), float(m["input_tp"])
    res["loudnorm_raw"] = m
    # --- 3. gain -----------------------------------------------------------------------
    gain = min(-16.0 - I, -1.5 - TP)
    gain_clamped = max(-30.0, min(30.0, gain))
    res["gain_db"] = round(gain_clamped, 3)
    res["gain_limited_by"] = "TP" if (-1.5 - TP) < (-16.0 - I) else "I"
    res["gain_clamped"] = gain != gain_clamped

    # --- 4. master ---------------------------------------------------------------------
    master = os.path.join(out, "master.flac")
    af = ("volume=%.3fdB,aresample=48000:resampler=soxr:precision=28,"
          "atrim=0:%d,apad=whole_dur=%d" % (gain_clamped, D, D))
    run("master flac", ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", raw, "-af", af,
                        "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", master])
    res["master_af"] = af
    res["master_probe"] = ffprobe(master)
    res["master_bytes"] = os.path.getsize(master)
    res["loudnorm_master"] = loudnorm_measure(master)

    # --- 5/6. padded s16le stream + segments -------------------------------------------
    pad = os.path.join(out, "pad.raw")
    run("decode padded s16le", ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", master,
                                "-af", "adelay=120|120,apad=pad_dur=0.02",
                                "-f", "s16le", "-ac", "2", "-ar", "48000", pad])
    pad_bytes = os.path.getsize(pad)
    res["pad_bytes"] = pad_bytes
    res["pad_samples"] = pad_bytes // 4
    res["pad_samples_expected"] = D * SR + LEAD_IN + LEAD_OUT
    bps = 2 * CH
    with open(pad, "rb") as f:
        padbuf = f.read()

    def cut(kind, k, start, nsamp, kbps, dest):
        chunk = padbuf[start * bps:(start + nsamp) * bps]
        assert len(chunk) == nsamp * bps, (kind, k, len(chunk))
        cmd = ["opusenc", "--raw", "--raw-rate", "48000", "--raw-chan", "2", "--bitrate", str(kbps),
               "--vbr", "--comp", "10", "--framesize", "20", "--discard-comments", "--quiet", "-", dest]
        run("opusenc %s %d" % (kind, k), cmd, stdin_bytes=chunk)
        decf = os.path.join(out, "dec", "%s_%03d.f32" % (kind, k))
        run("opusdec %s %d" % (kind, k), ["opusdec", "--quiet", "--rate", "48000", "--float", dest, decf])
        dec_samples = os.path.getsize(decf) // (4 * CH)
        return {"k": k, "pad_start_sample": start, "samples_in": nsamp, "bytes": os.path.getsize(dest),
                "kb": round(os.path.getsize(dest) / 1024, 1), "decoded_samples": dec_samples,
                "exact": dec_samples == nsamp}

    res["scrub"] = [cut("seg", k, k * SCRUB_HOP, SCRUB_SAMPLES, 48, os.path.join(out, "seg", "%04d.opus" % k))
                    for k in range(N_SCRUB)]
    res["listen"] = [cut("listen", k, k * LISTEN_HOP, LISTEN_SAMPLES, 96, os.path.join(out, "listen", "%03d.opus" % k))
                     for k in range(N_LISTEN)]

    # size check of --padding 0 (opusenc reserves 512 B of metadata padding by default)
    p0 = os.path.join(out, "dec", "seg_0000_pad0.opus")
    run("opusenc seg 0 --padding 0",
        ["opusenc", "--raw", "--raw-rate", "48000", "--raw-chan", "2", "--bitrate", "48", "--vbr", "--comp", "10",
         "--framesize", "20", "--discard-comments", "--padding", "0", "--quiet", "-", p0],
        stdin_bytes=padbuf[0:SCRUB_SAMPLES * bps])
    res["seg0_padding0_bytes"] = os.path.getsize(p0)

    # OpusHead pre-skip of segment 0 (bytes 28..29 of the file, little-endian, if the first page is OpusHead)
    with open(os.path.join(out, "seg", "0000.opus"), "rb") as f:
        head = f.read(64)
    i = head.find(b"OpusHead")
    res["opus_pre_skip"] = int.from_bytes(head[i + 10:i + 12], "little") if i >= 0 else None

    # --- monolithic reference for the seam test ----------------------------------------
    mono = os.path.join(out, "mono.opus")
    run("opusenc monolithic", ["opusenc", "--raw", "--raw-rate", "48000", "--raw-chan", "2", "--bitrate", "48",
                               "--vbr", "--comp", "10", "--framesize", "20", "--discard-comments", "--quiet", pad, mono])
    res["mono_bytes"] = os.path.getsize(mono)

    # --- bookkeeping -------------------------------------------------------------------
    res["steps"] = LOG
    tot = {}
    for e in LOG:
        key = e["step"].split()[0]
        t = tot.setdefault(key, {"wall_s": 0.0, "cpu_s": 0.0, "n": 0})
        t["wall_s"] += e["wall_s"]; t["cpu_s"] += e["cpu_s"]; t["n"] += 1
    res["totals_by_tool"] = {k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in tot.items()}
    res["total_wall_s"] = round(sum(e["wall_s"] for e in LOG), 3)
    res["total_cpu_s"] = round(sum(e["cpu_s"] for e in LOG), 3)
    if not a.keep_raw:
        os.unlink(raw)
        os.unlink(pad)
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: res[k] for k in ("engine", "D_s", "raw_duration_s", "gain_db", "total_wall_s", "total_cpu_s")}))


if __name__ == "__main__":
    main()
