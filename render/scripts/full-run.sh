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
# A song whose render aborted, whose manifest was refused (planned variants never rendered)
# or whose publish failed is skipped, listed at the end, and makes the exit status 1 (#11).
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

# run_logged <regex> <cmd…>: everything to the log, matching lines to the terminal, and the
# status of <cmd> ITSELF. The pipeline's own status is grep's 1 whenever nothing matched, which
# the old `|| true` hid — together with every real failure of the command in front of it.
run_logged() {
  local filter=$1 rc=0; shift
  "$@" 2>&1 | tee -a "$LOG" | grep -E "$filter" | tail -n 3 || rc=${PIPESTATUS[0]}
  return "$rc"
}

SKIPPED=()
for song in "${SONGS[@]}"; do
  log "=== $song: render (workers=$WORKERS)"
  rc=0
  run_logged '^\[sfr\] (finished|[0-9]+/[0-9]+)' "${SFR[@]}" render --song "$song" --workers "$WORKERS" || rc=$?
  # `sfr render` exits 1 whenever any job failed and `silent` failures are normal, so 1 is not
  # "aborted": the completeness check is `sfr manifest`, which refuses a song whose planned
  # variants were never rendered. Anything else (2 usage, 125+ docker/OOM-kill/signal) is.
  if (( rc != 0 && rc != 1 )); then
    log "!!! $song: render aborted (rc=$rc) — not manifesting or publishing"
    SKIPPED+=("$song")
    continue
  fi
  # manifest + publish are serialized across concurrent drivers (flock): two drivers publishing
  # at once could otherwise overwrite songs.json with each other's view of the world
  (
    flock 9
    MANIFEST=(manifest --song "$song")
    if [[ "$THOROUGH" == "1" ]]; then MANIFEST+=(--thorough); fi
    log "=== $song: ${MANIFEST[*]}"
    rc=0
    run_logged '^\[manifest\]' "${SFR[@]}" "${MANIFEST[@]}" || rc=$?
    if (( rc != 0 )); then
      log "!!! $song: manifest failed or refused (rc=$rc) — not publishing"
      exit 3
    fi
    log "=== $song: publish"
    rc=0
    (cd render && python3 -m sfr publish --out ../out --work ../work) 2>&1 | grep -vE '^(upload|Completed)' | tee -a "$LOG" | tail -n 2 || rc=${PIPESTATUS[0]}
    if (( rc != 0 )); then
      log "!!! $song: publish failed (rc=$rc)"
      exit 4
    fi
    log "=== $song: live"
  ) 9>work/publish.lock || { SKIPPED+=("$song"); continue; }
done
if (( ${#SKIPPED[@]} )); then
  log "!!! NOT published: ${SKIPPED[*]}"
  log "done with failures: ${SONGS[*]}"
  exit 1
fi
log "all done: ${SONGS[*]}"
