#!/usr/bin/env bash
# Link the deployment-specific files from a private "overlay" checkout into this tree.
#
#   scripts/overlay.sh [path-to-overlay]      # default: $SOUNDFONT_EXPLORER_OVERLAY, else ../soundfont-explorer-overlay
#
# The public repo carries no account ids, domains, state-bucket names or contact addresses. They
# live in a private repo whose tree mirrors this one; every file below is gitignored here and
# becomes a symlink into the overlay, so `terraform output -json > infra/live/outputs.json` and
# friends keep writing where the overlay keeps them. Templates: the matching *.example files.
set -euo pipefail
cd "$(dirname "$0")/.."
WANT=${1:-${SOUNDFONT_EXPLORER_OVERLAY:-../soundfont-explorer-overlay}}
OVERLAY=$(cd "$WANT" 2>/dev/null && pwd) || { echo "overlay checkout not found: $WANT" >&2; exit 1; }
FILES=(infra/live/backend.hcl infra/live/terraform.tfvars infra/live/outputs.json web/.env.local)
for f in "${FILES[@]}"; do
  if [[ -e "$OVERLAY/$f" ]]; then
    if [[ -e "$f" && ! -L "$f" ]]; then
      echo "!! $f exists here as a regular file — move it into $OVERLAY/$f first" >&2
      exit 1
    fi
    mkdir -p "$(dirname "$f")"
    ln -sfn "$OVERLAY/$f" "$f"
    echo "linked  $f -> $OVERLAY/$f"
  else
    echo "absent  $f (no $OVERLAY/$f; template: $f.example)"
  fi
done
