#!/bin/bash
# Provision the admin box. Executed at boot by the seed unit (which fetched this script
# from s3://<admin-bucket>/app/bootstrap.sh — deploy.sh keeps that copy current), with
# SFADMIN_BUCKET and AWS_DEFAULT_REGION in the environment. Re-runnable; every phase
# writes progress to /run/sfadmin/bootstrap.json, which the app's wait screen polls.
set -euo pipefail
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
}

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

# Caddy: seed persisted LE material (first-ever boot: empty, ~10 s issuance), our config
aws s3 sync "s3://$SFADMIN_BUCKET/caddy/" /var/lib/caddy/ --size-only || true
chown -R caddy:caddy /var/lib/caddy
sed "s/__HOSTNAME__/$SFADMIN_HOSTNAME/" "$APP/admin/caddy/Caddyfile" > /etc/caddy/Caddyfile
install -m 0755 "$APP/admin/scripts/sfadmin-update" /usr/local/sbin/sfadmin-update
cp "$APP"/admin/systemd/sfadmin*.service "$APP"/admin/systemd/sfadmin*.timer \
   "$APP"/admin/systemd/sfadmin*.path /etc/systemd/system/
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
chown -R sfadmin:sfadmin "$DATA" /var/cache/sfadmin

status ready 100 "ready"
