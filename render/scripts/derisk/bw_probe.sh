#!/bin/bash
# M0b side probe: does libopus narrow the coded bandwidth at 48 kbps stereo, and how does NSR vs the
# master move with bitrate?  Runs INSIDE sfr-render after run_chain.sh:
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/work/derisk:/derisk" \
#     -v "$PWD/render/scripts/derisk:/scripts:ro" sfr-render bash /scripts/bw_probe.sh
set -eu
cd /derisk/e1m1
for v in fluid adl58; do
  echo "=== $v"
  mkdir -p "$v/seamtmp"
  ffmpeg -hide_banner -nostats -y -i "$v/master.flac" -af "atrim=9.88:12.02,asetpts=PTS-STARTPTS" \
    -f s16le -ac 2 -ar 48000 "$v/seamtmp/sl5.s16" 2>/dev/null
  for cfg in "48" "48 --set-ctl-int 4008=1105" "64" "96"; do
    # shellcheck disable=SC2086  # $cfg is deliberately split into several opusenc arguments
    opusenc --raw --raw-rate 48000 --raw-chan 2 --bitrate $cfg --vbr --comp 10 --framesize 20 \
      --discard-comments --quiet "$v/seamtmp/sl5.s16" "$v/seamtmp/sl5.opus"
    opusdec --quiet --rate 48000 --no-dither "$v/seamtmp/sl5.opus" "$v/seamtmp/sl5dec.s16"
    sz=$(stat -c %s "$v/seamtmp/sl5.opus")
    for band in "lowpass=f=8000" "highpass=f=12500" "highpass=f=16500"; do
      m=$(ffmpeg -hide_banner -f s16le -ac 2 -ar 48000 -i "$v/seamtmp/sl5.s16" -af "$band,volumedetect" -f null - 2>&1 | grep mean_volume | awk '{print $5}')
      d=$(ffmpeg -hide_banner -f s16le -ac 2 -ar 48000 -i "$v/seamtmp/sl5dec.s16" -af "$band,volumedetect" -f null - 2>&1 | grep mean_volume | awk '{print $5}')
      echo "cfg[$cfg] size=$sz $band master=$m dB decoded=$d dB"
    done
    python3 - "$v/seamtmp/sl5.s16" "$v/seamtmp/sl5dec.s16" "$cfg" <<'PY'
import sys, array, math
a = array.array('h'); a.frombytes(open(sys.argv[1], 'rb').read())
b = array.array('h'); b.frombytes(open(sys.argv[2], 'rb').read())
n = min(len(a), len(b)); e = s = 0
for i in range(n):
    d = a[i] - b[i]; e += d * d; s += a[i] * a[i]
print("cfg[%s] samples %d/%d  NSR vs master = %.2f dB" % (sys.argv[3], len(b) // 2, len(a) // 2, 10 * math.log10(e / s)))
PY
  done
done
