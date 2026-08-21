#!/usr/bin/env bash
# Song-major render driver: render → manifest → publish one song at a time, so a song only
# appears on the site when it is complete (no pack churn) and each song is live as soon as it is.
#
#   render/scripts/full-run.sh freedoom-map01 [more song ids…]
#   render/scripts/full-run.sh --all                  # every song in songs.json (+ songs/private)
#   THOROUGH=1 …                                      # opusdec-validate every segment per song (slower)
#   WORKERS=32 …
#
# Restartable: sfr skips finished jobs. Run it in tmux; log goes to work/full-run.log.
set -euo pipefail
cd "$(dirname "$0")/../.."
WORKERS=${WORKERS:-32}
THOROUGH=${THOROUGH:-0}
LOG=${LOG:-work/full-run.log}
mkdir -p work out
SFR=(docker run --rm --user "$(id -u):$(id -g)"
     -v "$PWD/soundfonts:/fonts:ro" -v "$PWD/songs:/songs:ro" -v "$PWD/catalog:/catalog:ro"
     -v "$PWD/roms:/roms:ro" -v "$PWD/work:/work" -v "$PWD/out:/out"
     -v "$PWD/render/engines.json:/opt/engines.json:ro" -e SFR_ENGINES=/opt/engines.json sfr-render sfr)

if [[ "${1:-}" == "--all" ]]; then
  mapfile -t SONGS < <(python3 - <<'PY'
import json, os
ids = [s["id"] for s in json.load(open("songs/songs.json"))["songs"]]
p = "songs/private/songs.json"
if os.path.exists(p):
    ids += [s["id"] for s in json.load(open(p))["songs"]]
print("\n".join(ids))
PY
)
else
  SONGS=("$@")
fi
[[ ${#SONGS[@]} -gt 0 ]] || { echo "usage: $0 <song id…> | --all" >&2; exit 2; }

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

for song in "${SONGS[@]}"; do
  log "=== $song: render (workers=$WORKERS)"
  "${SFR[@]}" render --song "$song" --workers "$WORKERS" 2>&1 | tee -a "$LOG" | grep -E '^\[sfr\] (finished|[0-9]+/[0-9]+)' | tail -n 3 || true
  if [[ "$THOROUGH" == "1" ]]; then
    log "=== $song: manifest --thorough"
    "${SFR[@]}" manifest --song "$song" --thorough 2>&1 | tee -a "$LOG" | grep -E '^\[manifest\]'
  else
    log "=== $song: manifest"
    "${SFR[@]}" manifest --song "$song" 2>&1 | tee -a "$LOG" | grep -E '^\[manifest\]'
  fi
  log "=== $song: publish"
  (cd render && python3 -m sfr publish --out ../out --work ../work) 2>&1 | grep -vE '^(upload|Completed)' | tee -a "$LOG" | tail -n 2
  log "=== $song: live"
done
log "all done: ${SONGS[*]}"
