#!/usr/bin/env bash
# Launch the (single) admin box from the soundfont-explorer-admin launch template.
#
#   admin/scripts/up.sh
#
# The instance is ephemeral: it pulls everything from the admin bucket at boot and
# registers admin.<domain> itself. Terminate it with down.sh whenever it is not in use.
# Needs: aws CLI authenticated; infra/live/outputs.json with the `admin` output
# (terraform -chdir=infra/live apply -var enable_admin=true, then
#  terraform -chdir=infra/live output -json > infra/live/outputs.json)
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT=infra/live/outputs.json
[[ -f "$OUT" ]] || { echo "missing $OUT — run: terraform -chdir=infra/live output -json > $OUT" >&2; exit 1; }
read -r LT URL < <(python3 - "$OUT" <<'PY'
import json, sys
admin = json.load(open(sys.argv[1])).get("admin", {}).get("value")
if not admin:
    sys.exit("admin outputs are empty — apply with -var enable_admin=true and refresh outputs.json")
print(admin["launch_template"], admin["url"])
PY
)

RUNNING=$(aws ec2 describe-instances \
  --filters "Name=tag:project,Values=soundfont-explorer-admin" "Name=instance-state-name,Values=pending,running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n "$RUNNING" ]]; then
  echo "already up: $RUNNING ($URL)" >&2
  exit 1
fi

ID=$(aws ec2 run-instances \
  --launch-template "LaunchTemplateName=$LT,Version=\$Latest" \
  --count 1 \
  --query 'Instances[0].InstanceId' --output text)
echo "launched $ID, waiting for running..."
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "up: $ID at $IP"
echo "$URL will answer once bootstrap finishes (~2 min to the wait screen; ~3 min to ready)."
echo "progress: aws ssm start-session --target $ID   then: journalctl -u sfadmin-seed -u sfadmin-bootstrap -f"
