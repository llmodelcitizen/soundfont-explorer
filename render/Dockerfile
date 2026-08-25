# syntax=docker/dockerfile:1.7
# Soundfont Explorer render image — every synth engine, codec and tool the pipeline needs.
# Nothing engine-related is ever installed on the host; this image is the single
# source of truth for engine versions (see engines.json).
#
#   docker build -t sfr-render render/
#   docker build -t sfr-render --build-arg WITH_ROM_ENGINES=1 render/   # + Nuked-SC55, Munt (owner-supplied ROMs)
#
# Run with the repo root as $PWD (see docs/RENDER.md):
#   docker run --rm --user "$(id -u):$(id -g)" \
#     -v "$PWD/soundfonts:/fonts:ro" -v "$PWD/songs:/songs:ro" -v "$PWD/catalog:/catalog:ro" \
#     -v "$PWD/roms:/roms:ro" -v "$PWD/work:/work" -v "$PWD/out:/out" \
#     sfr-render sfr render --song freedoom-e1m1 --limit 5

# pinned by digest (multi-arch index) so a rebuild resolves to the same base; bump the digest
# deliberately, together with engines.json, rather than picking up whatever :trixie-slim is today
ARG DEBIAN_IMAGE=debian:trixie-slim@sha256:3a39a0592364683e6bab97937b72cad5a8fa6dcbbee90edb3bb48c7f8e94f258
ARG WITH_ROM_ENGINES=0

# ---------------------------------------------------------------------------
# builder: FM engines built from pinned git commits (M0: libADLMIDI; M2b: libOPNMIDI,
# libEDMIDI + edmidi-render). Everything lands in /opt/bin, /opt/banks, /opt/licenses.
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates pkg-config libasound2-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# libADLMIDI 1.6.2 (pinned commit; == vendored clone used by the MVP).
# MIDIPLAY_WAVE_ONLY removes the realtime player, and with it the `-w`/`-nl`
# flags: in this build adlmidiplay ALWAYS writes <input>.wav and never loops.
# Output rate is fixed at 44100 Hz (no CLI flag) — sfr resamples with soxr.
# utils/midiplay still compiles audio_alsa.c on Linux, hence libasound2-dev here
# and libasound2 in the runtime image.
ARG ADLMIDI_COMMIT=c46220909879dc552d7632467be553f4bf2a9e21
RUN git clone --filter=blob:none https://github.com/Wohlstand/libADLMIDI.git libADLMIDI \
 && git -C libADLMIDI checkout --quiet "${ADLMIDI_COMMIT}" \
 && cmake -S libADLMIDI -B libADLMIDI/build -DCMAKE_BUILD_TYPE=Release \
      -DlibADLMIDI_STATIC=ON -DlibADLMIDI_SHARED=OFF \
      -DWITH_MIDIPLAY=ON -DMIDIPLAY_WAVE_ONLY=ON -DWITH_MUS2MID=ON -DWITH_XMI2MID=ON \
      -DUSE_NUKED_EMULATOR=ON -DUSE_DOSBOX_EMULATOR=ON -DUSE_ESFMU_EMULATOR=ON \
      -DUSE_OPAL_EMULATOR=ON -DUSE_JAVA_EMULATOR=ON -DUSE_MAME_EMULATOR=ON -DUSE_YMFM_EMULATOR=ON \
 && cmake --build libADLMIDI/build -j"$(nproc)" \
 && install -Dm755 libADLMIDI/build/adlmidiplay /opt/bin/adlmidiplay \
 && install -Dm755 libADLMIDI/build/mus2mid /opt/bin/adlmidi-mus2mid \
 && install -Dm755 libADLMIDI/build/xmi2mid /opt/bin/adlmidi-xmi2mid \
 && mkdir -p /opt/licenses/libADLMIDI /opt/banks/wopl \
 && cp libADLMIDI/LICENSE libADLMIDI/fm_banks/LICENSE-*.txt /opt/licenses/libADLMIDI/ \
 && git -C libADLMIDI rev-parse HEAD > /opt/licenses/libADLMIDI/COMMIT

# libOPNMIDI 1.6.1 (pinned commit). Same WAVE_ONLY build as libADLMIDI: opnmidiplay has
# no `-w`/`-nl`, always writes <input>.wav next to the input, never loops, and renders at
# a fixed 44100 Hz. The bank is a POSITIONAL argument (`[bank.wopn] <file.mid>`), not -b.
# Cores: --emu-mame (YM2612, default) --emu-nuked[-3438|-2612] --emu-gens --emu-ymfm-opn2
# --emu-np2 --emu-mame-opna --emu-ymfm-opna. The 7 distinct fm_banks/*.wopn go to
# /opt/banks/wopn/<slug>.wopn with their readme files (slugs = catalog/opn_banks.json).
ARG OPNMIDI_COMMIT=3e6612377fbcf652056586b2562f00860c5bb0e7
RUN git clone --filter=blob:none https://github.com/Wohlstand/libOPNMIDI.git libOPNMIDI \
 && git -C libOPNMIDI checkout --quiet "${OPNMIDI_COMMIT}" \
 && cmake -S libOPNMIDI -B libOPNMIDI/build -DCMAKE_BUILD_TYPE=Release \
      -DlibOPNMIDI_STATIC=ON -DlibOPNMIDI_SHARED=OFF \
      -DWITH_MIDIPLAY=ON -DMIDIPLAY_WAVE_ONLY=ON -DWITH_MIDI2VGM=OFF -DUSE_VGM_FILE_DUMPER=OFF \
      -DUSE_MAME_EMULATOR=ON -DUSE_NUKED_EMULATOR=ON -DUSE_GENS_EMULATOR=ON -DUSE_YMFM_EMULATOR=ON \
      -DUSE_NP2_EMULATOR=ON -DUSE_MAME_2608_EMULATOR=ON \
      -DUSE_NUKED_OPN2_LLE_EMULATOR=OFF -DUSE_NUKED_OPNA_LLE_EMULATOR=OFF \
 && cmake --build libOPNMIDI/build -j"$(nproc)" \
 && install -Dm755 libOPNMIDI/build/opnmidiplay /opt/bin/opnmidiplay \
 && mkdir -p /opt/licenses/libOPNMIDI /opt/banks/wopn \
 && cp libOPNMIDI/LICENSE libOPNMIDI/LICENSE.*.txt libOPNMIDI/fm_banks/*.txt /opt/licenses/libOPNMIDI/ \
 && git -C libOPNMIDI rev-parse HEAD > /opt/licenses/libOPNMIDI/COMMIT \
 && cd libOPNMIDI/fm_banks \
 && install -m644 xg.wopn /opt/banks/wopn/xg.wopn \
 && install -m644 gm-old.wopn /opt/banks/wopn/gm-old.wopn \
 && install -m644 fmmidi.wopn /opt/banks/wopn/fmmidi.wopn \
 && install -m644 gs-by-papiezak-and-sneakernets.wopn /opt/banks/wopn/gs-dmxopn2.wopn \
 && install -m644 gems-fmlib-gmize.wopn /opt/banks/wopn/gems-fmlib-gmize.wopn \
 && install -m644 Nineko.wopn /opt/banks/wopn/nineko.wopn \
 && install -m644 Tomsoft.wopn /opt/banks/wopn/tomsoft.wopn \
 && install -m644 readme.txt /opt/banks/wopn/xg.readme.txt \
 && install -m644 readme.txt /opt/banks/wopn/gm-old.readme.txt \
 && install -m644 fmmidi-readme.txt /opt/banks/wopn/fmmidi.readme.txt \
 && install -m644 gs-by-papiezak-and-sneakernets-readme.txt /opt/banks/wopn/gs-dmxopn2.readme.txt \
 && install -m644 gems-fmlib-gmize-readme.txt /opt/banks/wopn/gems-fmlib-gmize.readme.txt \
 && install -m644 Nineko-readme.txt /opt/banks/wopn/nineko.readme.txt \
 && install -m644 Tomsoft-readme.txt /opt/banks/wopn/tomsoft.readme.txt

# libEDMIDI (pinned commit) as a static library + our edmidi-render front-end (edmidi-render/).
# The library has no chip-selection API (modules alternate OPLL/SCC, see edmidi-render/README.md),
# so modsel.cpp is compiled against the library's own src/ tree.
ARG EDMIDI_COMMIT=be48aebad667b0de538e81df8a986c10b76fd365
COPY edmidi-render/ /src/edmidi-render/
RUN git clone --filter=blob:none https://github.com/Wohlstand/libEDMIDI.git libEDMIDI \
 && git -C libEDMIDI checkout --quiet "${EDMIDI_COMMIT}" \
 && cmake -S libEDMIDI -B libEDMIDI/build -DCMAKE_BUILD_TYPE=Release \
      -DlibEDMIDI_STATIC=ON -DlibEDMIDI_SHARED=OFF -DWITH_MIDIPLAY=OFF \
 && cmake --build libEDMIDI/build -j"$(nproc)" \
 && cmake -S /src/edmidi-render -B /src/edmidi-render/build -DCMAKE_BUILD_TYPE=Release \
      -DEDMIDI_SOURCE_DIR=/src/libEDMIDI -DEDMIDI_LIBRARY=/src/libEDMIDI/build/libEDMIDI.a \
 && cmake --build /src/edmidi-render/build -j"$(nproc)" \
 && install -Dm755 /src/edmidi-render/build/edmidi-render /opt/bin/edmidi-render \
 && mkdir -p /opt/licenses/libEDMIDI \
 && cp libEDMIDI/LICENSE.txt libEDMIDI/AUTHORS /opt/licenses/libEDMIDI/ \
 && git -C libEDMIDI rev-parse HEAD > /opt/licenses/libEDMIDI/COMMIT

# Optional extra banks (with licence files) from the repo — see banks/README.md.
COPY banks/ /opt/banks/

# ---------------------------------------------------------------------------
# ROM engines (opt-in: --build-arg WITH_ROM_ENGINES=1). Both need owner-supplied
# ROM images under roms/<romset>/ at run time (docs/RENDER.md "Engines").
# rom-engines-0 is the default no-op stage; rom-engines-1 builds the tools.
# ---------------------------------------------------------------------------
FROM builder AS rom-engines-0
RUN mkdir -p /opt/rom/bin /opt/rom/licenses \
 && printf 'built without ROM engines (WITH_ROM_ENGINES=0)\n' > /opt/rom/licenses/README

FROM builder AS rom-engines-1
# Nuked-SC55 (jcmoyer fork): `nuked-sc55-render` only — the SDL2 GUI frontend is skipped
# (no SDL2 in the builder) but configure still requires rtmidi headers on Linux.
# Output rate is set by the romset: 66207 Hz (mk2/st/cm300/scb55/...) or 64000 Hz (mk1, jv880).
ARG NUKED_SC55_COMMIT=a48ef92e4bb85355a57a6ff2250d942f835f3503
# Munt: libmt32emu + mt32emu-smf2wav (needs GLib at build and run time). Native rate 32000 Hz.
ARG MUNT_COMMIT=6e7c01fba7e1d50c8fa705834889fd0eac136075
RUN apt-get update \
 && apt-get install -y --no-install-recommends librtmidi-dev libglib2.0-dev \
 && rm -rf /var/lib/apt/lists/* \
 && git clone --filter=blob:none https://github.com/jcmoyer/Nuked-SC55.git Nuked-SC55 \
 && git -C Nuked-SC55 checkout --quiet "${NUKED_SC55_COMMIT}" \
 && cmake -S Nuked-SC55 -B Nuked-SC55/build -DCMAKE_BUILD_TYPE=Release \
      -DNUKED_SOURCE="Soundfont Explorer render image" \
 && cmake --build Nuked-SC55/build -j"$(nproc)" --target nuked-sc55-render \
 && install -Dm755 Nuked-SC55/build/nuked-sc55-render /opt/rom/bin/nuked-sc55-render \
 && mkdir -p /opt/rom/licenses/Nuked-SC55 \
 && cp Nuked-SC55/LICENSE Nuked-SC55/doc/supported_romsets.md Nuked-SC55/doc/renderer_frontend.md /opt/rom/licenses/Nuked-SC55/ \
 && git -C Nuked-SC55 rev-parse HEAD > /opt/rom/licenses/Nuked-SC55/COMMIT \
 && git clone --filter=blob:none https://github.com/munt/munt.git munt \
 && git -C munt checkout --quiet "${MUNT_COMMIT}" \
 && cmake -S munt -B munt/build -DCMAKE_BUILD_TYPE=Release \
      -Dmunt_WITH_MT32EMU_QT=OFF -Dmunt_WITH_MT32EMU_SMF2WAV=ON \
      -Dlibmt32emu_SHARED=OFF -Dlibmt32emu_BUILD_TESTING=OFF \
 && cmake --build munt/build -j"$(nproc)" \
 && install -Dm755 munt/build/mt32emu_smf2wav/mt32emu-smf2wav /opt/rom/bin/mt32emu-smf2wav \
 && mkdir -p /opt/rom/licenses/munt \
 && cp munt/mt32emu/COPYING.txt /opt/rom/licenses/munt/COPYING.mt32emu.txt \
 && cp munt/mt32emu_smf2wav/COPYING.txt /opt/rom/licenses/munt/COPYING.smf2wav.txt \
 && git -C munt rev-parse HEAD > /opt/rom/licenses/munt/COMMIT

# hadolint ignore=DL3006
FROM rom-engines-${WITH_ROM_ENGINES} AS rom-engines

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS runtime
ARG WITH_ROM_ENGINES
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt
# timidity + freepats: TiMidity++ 2.14 with the FreePats GUS patch set. trixie's /etc/timidity/timidity.cfg
# sources the absent fluidr3_gm.cfg, so sfr always passes -c /etc/timidity/freepats.cfg. libglib2.0-0t64 is needed by mt32emu-smf2wav (it is already
# pulled in by fluidsynth/ffmpeg on trixie; the conditional keeps the ROM build honest if that changes).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fluidsynth timidity freepats ffmpeg opus-tools libasound2 python3 ca-certificates \
 && if [ "${WITH_ROM_ENGINES}" = "1" ]; then apt-get install -y --no-install-recommends libglib2.0-0t64; fi \
 && rm -rf /var/lib/apt/lists/* \
 && fluidsynth --version | head -1 && ffmpeg -hide_banner -version | head -1 && opusenc --version | head -1 \
 && timidity --version >/dev/null 2>&1 && test -r /etc/timidity/freepats.cfg

# s5cmd for bulk S3 transfer (font staging, publishing). Pinned by version + sha256; only the
# cloud path uses it, but it is 10 MB and keeps local and cloud on one image.
ARG S5CMD_VERSION=2.2.2
ARG S5CMD_SHA256=a15f83d2a6dc091e43b2a120f29f8f6c86d146c381766c0197ec75d7985af2b6
ADD https://github.com/peak/s5cmd/releases/download/v${S5CMD_VERSION}/s5cmd_${S5CMD_VERSION}_Linux-64bit.tar.gz /tmp/s5cmd.tgz
RUN echo "${S5CMD_SHA256}  /tmp/s5cmd.tgz" | sha256sum -c - \
 && tar -xzf /tmp/s5cmd.tgz -C /usr/local/bin s5cmd \
 && rm /tmp/s5cmd.tgz \
 && s5cmd version

COPY --from=builder /opt/bin/ /usr/local/bin/
COPY --from=builder /opt/licenses/ /opt/licenses/
COPY --from=builder /opt/banks/ /opt/banks/
COPY --from=rom-engines /opt/rom/bin/ /usr/local/bin/
COPY --from=rom-engines /opt/rom/licenses/ /opt/licenses/rom-engines/
COPY scripts/sfr /usr/local/bin/sfr
COPY sfr/ /opt/sfr/
# cloud shard runner (render/cloud/shard.py) — unused by local runs, tiny, keeps one image
COPY cloud/ /opt/cloud/
# the image carries the engines.json it was built from; the driver bind-mounts the checkout's copy over it
COPY engines.json /opt/engines.json
ENV SFR_ENGINES=/opt/engines.json

# default scratch location inside the container is the /work bind mount, never /tmp (tmpfs on the host)
ENV SFR_WORK=/work SFR_OUT=/out SFR_FONTS=/fonts SFR_SONGS=/songs SFR_CATALOG=/catalog SFR_ROMS=/roms SFR_BANKS=/opt/banks
WORKDIR /work
CMD ["sfr", "--help"]
