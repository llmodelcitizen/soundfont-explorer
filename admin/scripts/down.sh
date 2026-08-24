#!/usr/bin/env bash
# Terminate the admin box. Nothing on it is worth keeping: the library, run records and
# Caddy's certificates are all write-through to the admin bucket.
#
#   admin/scripts/down.sh
set -euo pipefail

IDS=$(aws ec2 describe-instances \
  --filters "Name=tag:project,Values=soundfont-explorer-admin" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -z "$IDS" ]]; then
  echo "nothing to terminate"
  exit 0
fi
# shellcheck disable=SC2086
aws ec2 terminate-instances --instance-ids $IDS --query 'TerminatingInstances[].[InstanceId,CurrentState.Name]' --output text
# shellcheck disable=SC2086
aws ec2 wait instance-terminated --instance-ids $IDS
echo "terminated: $IDS"
