#!/bin/sh
# M0b item 1 driver — run from the repo root on bimmer. Everything executes inside sfr-render.
#   render/scripts/derisk/run_chain.sh [fluid|adl58|all]
set -eu
cd "$(dirname "$0")/../../.."
mkdir -p work/derisk/e1m1
[ -f work/derisk/e1m1.mid ] || curl -sSL -o work/derisk/e1m1.mid \
  https://raw.githubusercontent.com/freedoom/freedoom/master/musics/d_e1m1.mid
FONT='GeneralUser GS 1.35.sf2'
dock() {
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD/soundfonts:/fonts:ro" \
    -v "$PWD/work/derisk:/derisk" \
    -v "$PWD/render/scripts/derisk:/scripts:ro" \
    sfr-render "$@"
}
what=${1:-all}
if [ "$what" = fluid ] || [ "$what" = all ]; then
  dock python3 /scripts/chain.py --out /derisk/e1m1/fluid --midi /derisk/e1m1.mid --engine fluid --font "/fonts/$FONT" &
fi
if [ "$what" = adl58 ] || [ "$what" = all ]; then
  dock python3 /scripts/chain.py --out /derisk/e1m1/adl58 --midi /derisk/e1m1.mid --engine adl --bank 58 &
fi
wait
