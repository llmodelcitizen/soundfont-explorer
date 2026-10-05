# infra

Terraform for everything on AWS (region us-east-1). The public site is an S3 bucket behind
CloudFront. Optional modules add the render fleet and the admin box. Cost guards (budgets, a circuit
breaker and a watchdog) are part of the same apply.

## Layout

| Path | What it does |
|---|---|
| `bootstrap/` | One-time: the S3 bucket that holds the live state. Local state. |
| `live/` | The real root. Wires up every module. |
| `modules/dns-cert` | ACM certificate for your domain, in an existing Route 53 zone |
| `modules/static-site` | Private site bucket + CloudFront |
| `modules/budget` | $10/month site budget (email only) |
| `modules/circuit-breaker` | Turns CloudFront **off** if egress gets too high |
| `modules/render-fleet` | Spot AWS Batch fleet, fonts bucket, ECR, watchdog. Off unless `enable_render_fleet`. |
| `modules/admin` | Admin bucket, launch template, IAM. Off unless `enable_admin`. |
| `modules/phase2` | Unused skeleton. Off unless `enable_phase2`. |

You need Terraform ≥ 1.11 (CI uses 1.15.9) and an authenticated AWS CLI.

## First-time setup

Link your private files first (see [`scripts/`](../scripts/README.md)):

```bash
scripts/overlay.sh ../soundfont-explorer-overlay
```

Create the state bucket. Keep `infra/bootstrap/terraform.tfstate` somewhere safe: it is not in git.

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply
```

Fill in `backend.hcl` and `terraform.tfvars` from the `*.example` files in `live/`, then:

```bash
terraform -chdir=infra/live init -backend-config=backend.hcl
terraform -chdir=infra/live apply
terraform -chdir=infra/live output -json > infra/live/outputs.json
```

AWS sends a few "confirm subscription" emails. **Click every one**, or alerts never arrive.

## Turning on the admin box or render fleet

Add the flags to `terraform.tfvars`, never as `-var`. An apply without the `-var` sees `false` and
**destroys** the module, including the ECR images and the watchdog.

```hcl
enable_admin        = true
enable_render_fleet = true
```

```bash
terraform -chdir=infra/live apply
```

## After every apply

Scripts all over the repo read `outputs.json`. Refresh it:

```bash
terraform -chdir=infra/live output -json > infra/live/outputs.json
```

If the outputs changed and the admin box is running, redeploy it and restart it with `down.sh` and
`up.sh` (see [`admin/`](../admin/README.md)).

## If the circuit breaker trips

You get an email: "CloudFront disabled by circuit breaker". The site is offline. The limits are
150 GB in the last 24 h or 900 GB month-to-date (`egress_daily_gb`, `egress_monthly_gb`).

First, look at the traffic and find out why:

```bash
DIST=$(jq -r .distribution_id.value infra/live/outputs.json)
aws cloudwatch get-metric-statistics --region us-east-1 \
  --namespace AWS/CloudFront --metric-name BytesDownloaded \
  --dimensions Name=DistributionId,Value=$DIST Name=Region,Value=Global \
  --start-time $(date -u -d '-2 days' +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 3600 --statistics Sum
```

Then turn the site back on. `terraform apply` will **not** do this.

```bash
aws cloudfront get-distribution-config --id $DIST > dc.json
jq '.DistributionConfig | .Enabled = true' dc.json > dc-on.json
aws cloudfront update-distribution --id $DIST \
  --if-match $(jq -r .ETag dc.json) --distribution-config file://dc-on.json
```

While either window is still over its limit, the hourly check trips the breaker again. Wait it out,
or raise the limit in `terraform.tfvars` and apply.

If the email says the breaker **FAILED** to disable CloudFront, run the same steps with
`.Enabled = false` to turn the site off by hand. Then check the Lambda's logs.

## If the render watchdog trips

You get an email: "render fleet watchdog tripped". Every 5 minutes the watchdog kills fleet instances
older than 4 hours and jobs older than 60 minutes, then turns the fleet off. Read the job logs to
find out why something ran so long. The next render run turns the fleet back on, so there is
nothing to re-enable.

Never tag your own resources `project=soundfont-explorer-render`, or the watchdog will kill them.

## Budgets

Budgets only send email. They never stop anything. The limits are site $10, admin $5 and render
$100 per month. Cost data can take up to 24 h to appear after the first apply.
