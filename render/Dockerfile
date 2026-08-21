# syntax=docker/dockerfile:1.7
# Soundfont Explorer render image — every synth engine, codec and tool the pipeline needs.
# Nothing engine-related is ever installed on the host; this image is the single
# source of truth for engine versions (see engines.json).
#
#   docker build -t sfr-render render/
#   docker build -t sfr-render --build-arg WITH_ROM_ENGINES=1 render/   # + Nuked-SC55, Munt (M2b)
#
# Run with the repo root as $PWD (see docs/RENDER.md):
#   docker run --rm --user "$(id -u):$(id -g)" \
#     -v "$PWD/soundfonts:/fonts:ro" -v "$PWD/songs:/songs:ro" -v "$PWD/catalog:/catalog:ro" \
#     -v "$PWD/roms:/roms:ro" -v "$PWD/work:/work" -v "$PWD/out:/out" \
#     sfr-render sfr render --song freedoom-e1m1 --limit 5

ARG DEBIAN_IMAGE=debian:trixie-slim

# ---------------------------------------------------------------------------
# builder: libADLMIDI (adlmidiplay, WAV-only) — M0
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
 && mkdir -p /opt/licenses/libADLMIDI \
 && cp libADLMIDI/LICENSE libADLMIDI/fm_banks/LICENSE-*.txt /opt/licenses/libADLMIDI/ \
 && git -C libADLMIDI rev-parse HEAD > /opt/licenses/libADLMIDI/COMMIT

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fluidsynth ffmpeg opus-tools libasound2 python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && fluidsynth --version | head -1 && ffmpeg -hide_banner -version | head -1 && opusenc --version | head -1

COPY --from=builder /opt/bin/ /usr/local/bin/
COPY --from=builder /opt/licenses/ /opt/licenses/
COPY scripts/sfr /usr/local/bin/sfr
COPY sfr/ /opt/sfr/

# default scratch location inside the container is the /work bind mount, never /tmp (tmpfs on the host)
ENV SFR_WORK=/work SFR_OUT=/out SFR_FONTS=/fonts SFR_SONGS=/songs SFR_CATALOG=/catalog SFR_ROMS=/roms
WORKDIR /work
CMD ["sfr", "--help"]
