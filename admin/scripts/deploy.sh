#!/usr/bin/env bash
# Build the admin SPA, bundle it with a repo snapshot + vendored wheels, upload to the
# admin bucket, and flip app/current. A new boot always uses the latest bundle; a running
# box picks it up via the UI's "update & restart" (POST /api/update).
#
#   admin/scripts/deploy.sh
#
# Deploys COMMITTED state (git archive HEAD) — commit before deploying.
# Needs: aws CLI authenticated; npm; pip3; infra/live/outputs.json with the `admin` output.
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=infra/live/outputs.json
[[ -f "$OUT" ]] || { echo "missing $OUT — run: terraform -chdir=infra/live output -json > $OUT" >&2; exit 1; }
read -r BUCKET HOSTNAME ZONE < <(python3 - "$OUT" <<'PY'
import json, sys
admin = json.load(open(sys.argv[1])).get("admin", {}).get("value")
if not admin:
    sys.exit("admin outputs are empty — apply with -var enable_admin=true and refresh outputs.json")
print(admin["bucket"], admin["hostname"], admin["zone_id"])
PY
)
GITSHA=$(git rev-parse --short HEAD)
[[ -z "$(git status --porcelain -- admin songs render catalog)" ]] \
  || echo "WARN: uncommitted changes in admin/songs/render/catalog will NOT be in the bundle (git archive HEAD)"

export PATH="$HOME/.local/bin:$PATH"
echo "== build SPA"
(cd admin/web && npm install --no-audit --no-fund --silent && npm run -s build)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
echo "== bundle (repo @ $GITSHA + dist + wheels)"
git archive HEAD | tar -x -C "$STAGE"
mkdir -p "$STAGE/admin/web/dist" "$STAGE/admin/wheels"
cp -r admin/web/dist/. "$STAGE/admin/web/dist/"
# wheels for the box: Debian 13 arm64 = CPython 3.13 on aarch64. Pure wheels always match;
# compiled ones (pydantic-core) need the explicit platform/abi tags.
pip3 download -q -r admin/server/requirements.txt -d "$STAGE/admin/wheels" \
  --only-binary :all: --implementation cp --python-version 3.13 \
  --abi cp313 --abi abi3 --abi none \
  --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux_2_28_aarch64
cat > "$STAGE/admin/bundle.env" <<ENV
SFADMIN_HOSTNAME=$HOSTNAME
SFADMIN_ZONE_ID=$ZONE
ENV
TAR="$STAGE.tar.gz"
tar -C "$STAGE" -czf "$TAR" .

KEY="app/sfadmin-$GITSHA.tar.gz"
echo "== upload s3://$BUCKET/$KEY ($(du -h "$TAR" | cut -f1))"
aws s3 cp --only-show-errors "$TAR" "s3://$BUCKET/$KEY"
printf '%s' "$KEY" | aws s3 cp --only-show-errors --content-type text/plain - "s3://$BUCKET/app/current"
aws s3 cp --only-show-errors admin/scripts/bootstrap.sh "s3://$BUCKET/app/bootstrap.sh"
rm -f "$TAR"
echo "done: $KEY is current. Running box: use the UI's update & restart; otherwise next up.sh boots it."
