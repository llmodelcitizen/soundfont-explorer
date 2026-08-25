#!/bin/bash
# Provision the admin box. Executed at boot by the seed unit (which fetched this script
# from s3://<admin-bucket>/app/bootstrap.sh — deploy.sh keeps that copy current), with
# SFADMIN_BUCKET and AWS_DEFAULT_REGION in the environment. Re-runnable; every phase
# writes progress to /run/sfadmin/bootstrap.json, which the app's wait screen polls.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

: "${SFADMIN_BUCKET:?set by the seed unit}"
REGION=${AWS_DEFAULT_REGION:-us-east-1}
APP=/opt/sfadmin/app
DATA=/opt/sfadmin/data
VENV=/opt/sfadmin/venv
STATUS_DIR=/run/sfadmin
STATUS=$STATUS_DIR/bootstrap.json

mkdir -p "$STATUS_DIR"
status() {
  printf '{"phase":"%s","pct":%d,"msg":"%s"}\n' "$1" "$2" "$3" > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
  echo "bootstrap: $1 ($2%) $3"
  PHASE=$1
}
# A phase that dies must say so on the wait screen, not sit at its last percentage forever.
# The status file doubles as the app's gate: routes that touch the library (canon runs
# included) 503 until it says ready, so a box whose restore failed can never persist a
# stale songs/ tree over the bucket copy (#16).
PHASE=start
trap 'status failed 0 "bootstrap died during $PHASE (journalctl -u sfadmin-seed)"' ERR

status packages 5 "installing packages"
apt-get update -qq
apt-get install -y -qq caddy fluidsynth ffmpeg python3-venv python3-pip curl

# 1 GiB swap backstop: preview render + zip stream + library sync at once can brush the
# t4g.micro's 1 GiB
if ! swapon --show --noheadings | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
fi

# SSM agent for the break-glass shell (Debian AMIs don't ship it); best-effort
if ! systemctl is-active -q amazon-ssm-agent 2>/dev/null; then
  (curl -fsSL -o /tmp/ssm.deb "https://s3.$REGION.amazonaws.com/amazon-ssm-$REGION/latest/debian_arm64/amazon-ssm-agent.deb" \
    && dpkg -i /tmp/ssm.deb >/dev/null && systemctl enable --now amazon-ssm-agent) \
    || echo "WARN: SSM agent install failed (no break-glass shell)"
fi

status bundle 20 "fetching app bundle"
KEY=$(aws s3 cp "s3://$SFADMIN_BUCKET/app/current" -)
rm -rf "$APP.new" && mkdir -p "$APP.new"
aws s3 cp "s3://$SFADMIN_BUCKET/$KEY" - | tar -xz -C "$APP.new"
rm -rf "$APP" && mv "$APP.new" "$APP"
# Root's own copy of the things root itself will run, taken while the tree is still
# root-owned: $APP is chowned to sfadmin below, and on a REBOOT sfadmin.service starts
# alongside this unit (both are WantedBy=multi-user.target, with nothing ordering them),
# so the app could rewrite a unit file between that chown and the install further down and
# have root activate it. Same boundary as sfadmin-update's, same reasoning (#9, #19).
ROOTSTAGE=$(mktemp -d)
trap 'rm -rf "$ROOTSTAGE"' EXIT
mkdir -p "$ROOTSTAGE/systemd"
cp -r "$APP/admin/systemd/." "$ROOTSTAGE/systemd/"
cp -r "$APP/admin/scripts/sfadmin-update" "$ROOTSTAGE/sfadmin-update"
# hostname + zone id, written by deploy.sh from infra/live/outputs.json
# shellcheck source=/dev/null
. "$APP/admin/bundle.env"

status venv 35 "python environment"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --no-index --find-links "$APP/admin/wheels" \
  -r "$APP/admin/server/requirements.txt"

status dns 45 "registering $SFADMIN_HOSTNAME"
TOK=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
IP=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOK" \
  http://169.254.169.254/latest/meta-data/public-ipv4)
CHANGE=$(aws route53 change-resource-record-sets --hosted-zone-id "$SFADMIN_ZONE_ID" \
  --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$SFADMIN_HOSTNAME\",\"Type\":\"A\",\"TTL\":60,\"ResourceRecords\":[{\"Value\":\"$IP\"}]}}]}" \
  --query 'ChangeInfo.Id' --output text)
aws route53 wait resource-record-sets-changed --id "$CHANGE"

status services 60 "starting caddy + app"
id -u sfadmin >/dev/null 2>&1 || useradd -r -m -d /var/lib/sfadmin -s /usr/sbin/nologin sfadmin
mkdir -p "$DATA" /var/cache/sfadmin
chgrp sfadmin "$STATUS_DIR" && chmod 775 "$STATUS_DIR"   # app touches update-requested here
# canon.py runs as sfadmin inside the snapshot (writes songs/songs.json, songs/rendered/,
# corpus-imports.json) and reads sources at songs/import/FILES — point that at the library
chown -R sfadmin:sfadmin "$APP"
# the venv is sfadmin's too: sfadmin-update re-installs the bundle's wheels unprivileged,
# so a bundle can never run pip hooks as root (#9)
chown -R sfadmin:sfadmin "$VENV"
rm -rf "$APP/songs/import/FILES"
mkdir -p "$APP/songs/import"
ln -sfn "$DATA/library/FILES" "$APP/songs/import/FILES"

cat > /etc/sfadmin.env <<ENV
SFADMIN_BUCKET=$SFADMIN_BUCKET
SFADMIN_HOSTNAME=$SFADMIN_HOSTNAME
SFADMIN_ZONE_ID=$SFADMIN_ZONE_ID
SFADMIN_SITE_BUCKET=${SFADMIN_SITE_BUCKET:-}
SFADMIN_DISTRIBUTION=${SFADMIN_DISTRIBUTION:-}
SFADMIN_FONTS_BUCKET=${SFADMIN_FONTS_BUCKET:-}
SFADMIN_JOB_QUEUE=${SFADMIN_JOB_QUEUE:-}
SFADMIN_JOB_DEFINITION=${SFADMIN_JOB_DEFINITION:-}
SFADMIN_COMPUTE_ENV=${SFADMIN_COMPUTE_ENV:-}
SFADMIN_LOG_GROUP=${SFADMIN_LOG_GROUP:-}
SFADMIN_REPO=$APP
SFADMIN_DATA=$DATA
SFADMIN_CACHE=/var/cache/sfadmin
SFADMIN_STATUS=$STATUS
AWS_DEFAULT_REGION=$REGION
ENV

# Caddy: seed persisted LE material (first-ever boot: empty, ~10 s issuance), our config.
# No --size-only in either direction: a renewed certificate and its regenerated key are the
# same size as the pair they replace, and this unit runs on every boot — on a box whose
# /var/lib/caddy holds the expired pair, a size-only restore keeps it and Caddy re-issues,
# burning the LE rate limit this sync exists to protect (#19).
aws s3 sync "s3://$SFADMIN_BUCKET/caddy/" /var/lib/caddy/ || true
chown -R caddy:caddy /var/lib/caddy
sed "s/__HOSTNAME__/$SFADMIN_HOSTNAME/" "$APP/admin/caddy/Caddyfile" > /etc/caddy/Caddyfile
# from $ROOTSTAGE, never from $APP: see the copy made before the chown above
install -o root -g root -m 0755 "$ROOTSTAGE/sfadmin-update" /usr/local/sbin/sfadmin-update
install -o root -g root -m 0644 -t /etc/systemd/system "$ROOTSTAGE"/systemd/*
systemctl daemon-reload
systemctl enable -q caddy sfadmin.service sfadmin-caddy-sync.timer sfadmin-update.path
systemctl restart caddy sfadmin.service
systemctl start sfadmin-caddy-sync.timer sfadmin-update.path

status library 75 "syncing MIDI library"
mkdir -p "$DATA/library"
aws s3 sync "s3://$SFADMIN_BUCKET/library/FILES/" "$DATA/library/FILES/" --size-only
aws s3 cp "s3://$SFADMIN_BUCKET/library/library.json" "$DATA/library/library.json" \
  || echo "WARN: no library.json in the bucket yet (run admin/scripts/ingest.py)"
aws s3 cp "s3://$SFADMIN_BUCKET/assets/gm.sf2" "$DATA/gm.sf2" \
  || echo "WARN: no assets/gm.sf2 in the bucket (previews disabled) — see docs/ADMIN.md"
# canon products (songs.json, canonical MIDIs, fragment, report) are derived state the
# app persists to canon/ after each run; without this restore the render list resets to
# the bundle's committed 25-song stub on every boot. A restore that fails must fail the
# boot (#16): the app would otherwise run canon over the stub and persist that. Only a
# key that does not exist yet (fresh bucket, no Canon check so far) is tolerated.
aws s3 sync "s3://$SFADMIN_BUCKET/canon/rendered/" "$APP/songs/rendered/" --size-only
for f in songs.json corpus-imports.json canon-report.json; do
  if ! err=$(aws s3 cp "s3://$SFADMIN_BUCKET/canon/$f" "$APP/songs/$f" --only-show-errors 2>&1); then
    case $err in
      *"(404)"*|*NoSuchKey*) echo "note: no canon/$f in the bucket yet (run a Canon check)" ;;
      *) echo "$err" >&2; false ;;
    esac
  fi
done
chown -R sfadmin:sfadmin "$APP/songs" "$DATA" /var/cache/sfadmin

status ready 100 "ready"
