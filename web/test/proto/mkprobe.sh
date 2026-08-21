#!/bin/sh
# Regenerates probe-1k-40ms.opus (40 ms, 1 kHz, 0.5 amplitude sine, 48 kHz stereo, 48 kbps Ogg Opus)
# inside the sfr-render image. Run from the repo root.
set -e
mkdir -p work/proto
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/work/proto:/p" sfr-render sh -c '
  ffmpeg -loglevel error -y -f lavfi -i "aevalsrc=0.5*sin(2*PI*1000*t):s=48000:c=stereo:d=0.04" -f s16le - \
  | opusenc --raw --raw-rate 48000 --raw-chan 2 --bitrate 48 --vbr --comp 10 --framesize 20 --discard-comments --quiet - /p/probe-1k-40ms.opus'
cp work/proto/probe-1k-40ms.opus web/test/proto/probe-1k-40ms.opus
ls -l web/test/proto/probe-1k-40ms.opus
