#!/usr/bin/env bash
# Build the client and upload it (plan §13 M4):
#   hashed assets  → immutable, 1 year
#   index.html     → 60 s (+ stale-while-revalidate)
#   404.html       → 60 s
# then invalidate / and /index.html. Audio/manifests are published by `sfr publish`, not here.
#
#   web/scripts/deploy.sh [--dry-run]
# Needs: aws CLI authenticated; infra/live/outputs.json (terraform -chdir=infra/live output -json > infra/live/outputs.json)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(cd .. && pwd)
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dryrun)

OUT="$ROOT/infra/live/outputs.json"
[[ -f "$OUT" ]] || { echo "missing $OUT — run: terraform -chdir=infra/live output -json > infra/live/outputs.json" >&2; exit 1; }
BUCKET=$(python3 -c "import json;print(json.load(open('$OUT'))['bucket']['value'])")
DIST=$(python3 -c "import json;print(json.load(open('$OUT'))['distribution_id']['value'])")

export PATH="$HOME/.local/bin:$PATH"
npm run -s build

IMMUTABLE="public,max-age=31536000,immutable"
SHORT="public,max-age=60,stale-while-revalidate=600"

echo "== assets (immutable)"
aws s3 sync dist/assets/ "s3://$BUCKET/assets/" --cache-control "$IMMUTABLE" --size-only "${DRY[@]}"
echo "== index.html / 404.html (60 s)"
aws s3 cp dist/index.html "s3://$BUCKET/index.html" --content-type "text/html; charset=utf-8" --cache-control "$SHORT" "${DRY[@]}"
aws s3 cp dist/404.html "s3://$BUCKET/404.html" --content-type "text/html; charset=utf-8" --cache-control "$SHORT" "${DRY[@]}"
if [[ -f dist/robots.txt ]]; then aws s3 cp dist/robots.txt "s3://$BUCKET/robots.txt" --content-type text/plain --cache-control "$SHORT" "${DRY[@]}"; fi
if [[ ${#DRY[@]} -eq 0 ]]; then
  echo "== invalidate"
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/" "/index.html" "/404.html" --query 'Invalidation.Id' --output text
fi
echo "done: https://soundfonts.ericq.com/"
