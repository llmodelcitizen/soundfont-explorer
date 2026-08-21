/*
 * edmidi-render — render a MIDI file once through libEDMIDI (Emu De MIDI:
 * YM2413 OPLL + Konami SCC emulation) to a WAV file.
 *
 *   edmidi-render [-r 48000] [-m opll|scc|all] [-n 8] [-f s16|f32] [-g 1.0] -o out.wav in.mid
 *
 * Facts about the library this tool relies on (libEDMIDI @ the commit pinned in
 * render/engines.json, include/emu_de_midi.h + src/CSMFPlay.cpp):
 *   - edmidi_initEx(rate, modules): `modules` is an even COUNT (2..16), not a
 *     mask. Module i is an OPLL when i is even and an SCC when i is odd. Every
 *     melodic note is sent to one OPLL module and one SCC module (layered);
 *     percussion (channel 10) goes to the OPLL rhythm section only.
 *   - There is no public API to pick chips. `-m opll` / `-m scc` therefore swap
 *     the unwanted modules for a silent device (see modsel.cpp); `-m all` is the
 *     library's own layering. PSG (emu2149 / CPSGDrum) is compiled into the
 *     library but never attached to a module, so there is no PSG voice to render.
 *   - edmidi_playF32(dev, n, buf) fills n floats (interleaved stereo) and returns
 *     the number of BYTES produced; 0 means the song ended (loop disabled).
 *     Module outputs are summed without clipping, so peaks may exceed +/-1.0:
 *     `-f f32` keeps them, `-f s16` saturates.
 *
 * Output: RIFF/WAVE, stereo, at the requested rate; the whole song once, no loop.
 * Exit status: 0 on success, 1 on engine/IO error, 2 on bad arguments.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

#include "emu_de_midi.h"

/* modsel.cpp */
int edmidi_render_select(struct EDMIDIPlayer *dev, const char *mode, int modules);

#define FRAMES_PER_CALL 4096

static void usage(FILE *f)
{
    fputs("Usage: edmidi-render [options] -o <out.wav> <in.mid>\n"
          "  -r <hz>           output sample rate (default 48000)\n"
          "  -m opll|scc|all   chips to render: OPLL only, SCC only, or the library's\n"
          "                    OPLL+SCC layering (default all). There is no PSG voice.\n"
          "  -n <modules>      even number of chip modules, 2..16 (default 8 = 4 OPLL + 4 SCC)\n"
          "  -f s16|f32        WAV sample format (default s16; f32 never clips)\n"
          "  -g <gain>         linear gain applied before writing (default 1.0)\n"
          "  -o <file>         output WAV path (required)\n"
          "  -h                this help\n", f);
}

static void put_u16(FILE *f, unsigned v) { unsigned char b[2] = { (unsigned char)v, (unsigned char)(v >> 8) }; fwrite(b, 1, 2, f); }
static void put_u32(FILE *f, uint32_t v) { unsigned char b[4] = { (unsigned char)v, (unsigned char)(v >> 8), (unsigned char)(v >> 16), (unsigned char)(v >> 24) }; fwrite(b, 1, 4, f); }

static void write_wav_header(FILE *f, long rate, int is_float, uint32_t data_bytes)
{
    const unsigned channels = 2;
    const unsigned bits = is_float ? 32 : 16;
    const unsigned block = channels * bits / 8;
    fseek(f, 0, SEEK_SET);
    fwrite("RIFF", 1, 4, f);
    put_u32(f, 36 + data_bytes);
    fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f);
    put_u32(f, 16);
    put_u16(f, is_float ? 3 : 1);            /* WAVE_FORMAT_IEEE_FLOAT : WAVE_FORMAT_PCM */
    put_u16(f, channels);
    put_u32(f, (uint32_t)rate);
    put_u32(f, (uint32_t)rate * block);
    put_u16(f, block);
    put_u16(f, bits);
    fwrite("data", 1, 4, f);
    put_u32(f, data_bytes);
}

int main(int argc, char **argv)
{
    long rate = 48000;
    int modules = 8;
    int is_float = 0;
    double gain = 1.0;
    const char *mode = "all";
    const char *out_path = NULL;
    const char *in_path = NULL;
    int i;

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (!strcmp(a, "-h") || !strcmp(a, "--help")) { usage(stdout); return 0; }
        else if (!strcmp(a, "-r") && i + 1 < argc) rate = atol(argv[++i]);
        else if (!strcmp(a, "-m") && i + 1 < argc) mode = argv[++i];
        else if (!strcmp(a, "-n") && i + 1 < argc) modules = atoi(argv[++i]);
        else if (!strcmp(a, "-g") && i + 1 < argc) gain = atof(argv[++i]);
        else if (!strcmp(a, "-o") && i + 1 < argc) out_path = argv[++i];
        else if (!strcmp(a, "-f") && i + 1 < argc) {
            const char *fmt = argv[++i];
            if (!strcmp(fmt, "s16")) is_float = 0;
            else if (!strcmp(fmt, "f32")) is_float = 1;
            else { fprintf(stderr, "edmidi-render: unknown format %s (s16|f32)\n", fmt); return 2; }
        }
        else if (a[0] == '-' && a[1] != '\0') { fprintf(stderr, "edmidi-render: unknown option %s\n", a); usage(stderr); return 2; }
        else if (!in_path) in_path = a;
        else { fprintf(stderr, "edmidi-render: unexpected argument %s\n", a); return 2; }
    }
    if (!in_path || !out_path) { usage(stderr); return 2; }
    if (rate < 8000 || rate > 384000) { fprintf(stderr, "edmidi-render: bad sample rate %ld\n", rate); return 2; }
    if (modules < 2 || modules > 16 || (modules & 1)) { fprintf(stderr, "edmidi-render: -n must be even and within 2..16\n"); return 2; }
    if (strcmp(mode, "all") && strcmp(mode, "opll") && strcmp(mode, "scc")) {
        fprintf(stderr, "edmidi-render: -m must be opll, scc or all (libEDMIDI has no PSG voice: "
                        "emu2149/CPSGDrum is compiled but never attached to a module)\n");
        return 2;
    }

    struct EDMIDIPlayer *dev = edmidi_initEx(rate, modules);
    if (!dev) { fprintf(stderr, "edmidi-render: init failed: %s\n", edmidi_errorString()); return 1; }

    if (edmidi_render_select(dev, mode, modules) != 0) {
        fprintf(stderr, "edmidi-render: module selection '%s' failed\n", mode);
        edmidi_close(dev);
        return 1;
    }

    edmidi_setLoopEnabled(dev, 0);
    if (edmidi_openFile(dev, in_path) < 0) {
        fprintf(stderr, "edmidi-render: cannot open %s: %s\n", in_path, edmidi_errorInfo(dev));
        edmidi_close(dev);
        return 1;
    }
    edmidi_setLoopEnabled(dev, 0);   /* openFile resets the player; make sure loop stays off */

    FILE *out = fopen(out_path, "wb");
    if (!out) { perror(out_path); edmidi_close(dev); return 1; }
    write_wav_header(out, rate, is_float, 0);   /* placeholder, patched at the end */

    fprintf(stderr, "edmidi-render: libEDMIDI %s, %ld Hz, %d modules (%s), song length %.3f s\n",
            edmidi_linkedLibraryVersion(), rate, modules, mode, edmidi_totalTimeLength(dev));

    float fbuf[FRAMES_PER_CALL * 2];
    int16_t sbuf[FRAMES_PER_CALL * 2];
    uint64_t frames_total = 0;
    double peak = 0.0;
    uint32_t data_bytes = 0;

    for (;;) {
        int got = edmidi_playF32(dev, FRAMES_PER_CALL * 2, fbuf);
        if (got <= 0) break;                         /* 0 = end of song (loop disabled) */
        int frames = got / (int)(2 * sizeof(float)); /* edmidi_playF32 returns bytes */
        if (frames <= 0) break;
        int n = frames * 2;
        for (i = 0; i < n; i++) {
            double v = (double)fbuf[i] * gain;
            double av = fabs(v);
            if (av > peak) peak = av;
            if (is_float) fbuf[i] = (float)v;
            else {
                if (v > 1.0) v = 1.0; else if (v < -1.0) v = -1.0;
                sbuf[i] = (int16_t)lrint(v * 32767.0);
            }
        }
        size_t bytes = is_float ? (size_t)n * sizeof(float) : (size_t)n * sizeof(int16_t);
        if (fwrite(is_float ? (void *)fbuf : (void *)sbuf, 1, bytes, out) != bytes) {
            perror("edmidi-render: write");
            fclose(out); edmidi_close(dev);
            return 1;
        }
        data_bytes += (uint32_t)bytes;
        frames_total += (uint64_t)frames;
    }

    write_wav_header(out, rate, is_float, data_bytes);
    if (fclose(out) != 0) { perror("edmidi-render: close"); edmidi_close(dev); return 1; }
    edmidi_close(dev);

    fprintf(stderr, "edmidi-render: wrote %llu frames (%.3f s) to %s, peak %.3f%s\n",
            (unsigned long long)frames_total, (double)frames_total / (double)rate, out_path, peak,
            (!is_float && peak > 1.0) ? " (clipped; use -f f32)" : "");
    return 0;
}
