#!/usr/bin/env bash
# Follow the admin box's journal live from this machine (no SSH — SSM):
#
#   admin/scripts/logs.sh              # the sfadmin units + caddy
#   admin/scripts/logs.sh --all        # the entire journal (kernel, apt, ssm, ...)
#
# Needs: aws CLI authenticated + the Session Manager plugin
# (https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).
# What you'll see and where the rest lives: docs/ADMIN.md "Watching the box".
set -euo pipefail

ID=$(aws ec2 describe-instances \
  --filters "Name=tag:project,Values=soundfont-explorer-admin" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
[[ "$ID" != "None" && -n "$ID" ]] || { echo "no running admin box (admin/scripts/up.sh)" >&2; exit 1; }

if [[ "${1:-}" == "--all" ]]; then
  JCMD="sudo journalctl -f -n 100"
else
  JCMD="sudo journalctl -f -n 100 -u sfadmin -u caddy -u sfadmin-seed -u sfadmin-update -u sfadmin-caddy-sync"
fi

exec aws ssm start-session --target "$ID" \
  --document-name AWS-StartInteractiveCommand \
  --parameters "{\"command\":[\"$JCMD\"]}"
