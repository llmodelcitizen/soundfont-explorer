# Admin box: one tiny ephemeral EC2 instance serving the admin web UI (admin/).
#
# Everything durable lives here (bucket, IAM, launch template); the instance itself is
# launched and terminated by admin/scripts/{up,down}.sh and holds no state worth keeping —
# the versioned admin bucket is the source of truth for the MIDI library, the app bundle,
# render run records and Caddy's Let's Encrypt material. Idle cost is the bucket alone
# (~130 MB, effectively $0). There is deliberately no EIP and no Route 53 record in
# Terraform: the box upserts admin.<domain>'s A record from IMDS at boot, so a stopped
# admin costs nothing and Terraform never fights a dynamic IP.
#
# The instance is tagged project=soundfont-explorer-admin — its own budget, and NOT the
# render fleet's tag: the fleet watchdog unconditionally terminates anything tagged
# project=soundfont-explorer-render older than max_instance_minutes.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0", configuration_aliases = [aws] }
  }
}

variable "alert_email" { type = string }
variable "site_bucket" { type = string }
variable "distribution_id" { type = string }
variable "zone_id" { type = string }

variable "hostname" {
  type        = string
  description = "FQDN the box serves and is allowed to upsert in Route 53 (admin.<domain>)."
}

variable "render" {
  type = object({
    fonts_bucket        = string
    job_queue           = string
    job_definition      = string
    compute_environment = string
    log_group           = string
  })
  default     = null
  description = "Render-fleet resource names (null when the fleet is disabled: the admin UI then has no Batch permissions and render submission is unavailable)."
}

variable "instance_type" {
  type    = string
  default = "t4g.micro"
}

variable "root_volume_gb" {
  type        = number
  default     = 8
  description = "Library (~134 MB) + venv + preview cache (500 MB LRU); 8 is plenty."
}

variable "budget_limit_usd" {
  type        = number
  default     = 5
  description = "Monthly budget for project=soundfont-explorer-admin. 24/7 would be ~$10.4/mo; the box is meant to be up hours at a time."
}

locals {
  name = "soundfont-explorer-admin"
  tags = { project = "soundfont-explorer-admin" }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

# Debian's official AWS account. arm64 for t4g; the box only needs apt packages
# (caddy, fluidsynth, ffmpeg, python3) that all exist on arm64.
data "aws_ami" "debian" {
  most_recent = true
  owners      = ["136693071363"]
  filter {
    name   = "name"
    values = ["debian-13-arm64-*"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

# ---------------------------------------------------------------- bucket

# Versioned, unlike the fonts bucket: the MIDI library is irreplaceable master data and the
# admin UI can rename and delete — versioning is the undo. Standard storage; the whole
# library is ~134 MB.
resource "aws_s3_bucket" "admin" {
  bucket = "${local.name}-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_versioning" "admin" {
  bucket = aws_s3_bucket.admin.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "admin" {
  bucket                  = aws_s3_bucket.admin.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "admin" {
  bucket = aws_s3_bucket.admin.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning without a cap would keep every superseded library.json forever; 90 days of
# undo is the point, not an archive.
resource "aws_s3_bucket_lifecycle_configuration" "admin" {
  bucket = aws_s3_bucket.admin.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

# ---------------------------------------------------------------- security group

resource "aws_security_group" "admin" {
  name        = local.name
  description = "Soundfont Explorer admin box: HTTP(S) in (Caddy terminates TLS), no SSH (break-glass is SSM)"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    description      = "ACME HTTP-01 + redirect to https"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  ingress {
    description      = "admin UI"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  tags = merge(local.tags, { Name = local.name })
}

# ---------------------------------------------------------------- IAM

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  tags               = local.tags
}

# Break-glass shell (the SG has no SSH): aws ssm start-session --target <id>
resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id
  batch_arns = var.render == null ? null : "arn:aws:batch:${local.region}:${local.account_id}"
  render_on  = var.render == null ? [] : [1]
}

data "aws_iam_policy_document" "instance" {
  # --- admin bucket: full read/write including versions (versioning is the undo story)
  statement {
    actions = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject",
    "s3:ListBucket", "s3:ListBucketVersions"]
    resources = [aws_s3_bucket.admin.arn, "${aws_s3_bucket.admin.arn}/*"]
  }

  # --- site bucket: read manifests, publish songs.json + catalog docs, delete renders.
  # Deliberately NO write on a/* or s/* content (only the shards publish audio) and no
  # delete on anything else — the deletes below are the "remove a track" / prune surface.
  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.site_bucket}"]
  }
  statement {
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.site_bucket}/s/*",
      "arn:aws:s3:::${var.site_bucket}/c/*",
      "arn:aws:s3:::${var.site_bucket}/songs.json",
    ]
  }
  statement {
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.site_bucket}/songs.json",
      "arn:aws:s3:::${var.site_bucket}/c/*",
    ]
  }
  statement {
    actions = ["s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::${var.site_bucket}/a/*",
      "arn:aws:s3:::${var.site_bucket}/s/*",
    ]
  }

  # --- render staging + Batch, only when the fleet exists
  dynamic "statement" {
    for_each = local.render_on
    content {
      actions   = ["s3:ListBucket"]
      resources = ["arn:aws:s3:::${var.render.fonts_bucket}"]
    }
  }
  dynamic "statement" {
    for_each = local.render_on
    content {
      actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      resources = [
        "arn:aws:s3:::${var.render.fonts_bucket}/songs/*",
        "arn:aws:s3:::${var.render.fonts_bucket}/catalog/*",
        "arn:aws:s3:::${var.render.fonts_bucket}/shards.json",
      ]
    }
  }
  dynamic "statement" {
    for_each = local.render_on
    content {
      # Describe*/List* are not resource-scopable; TerminateJob matches the watchdog precedent.
      actions   = ["batch:DescribeJobs", "batch:ListJobs", "batch:DescribeComputeEnvironments", "batch:TerminateJob"]
      resources = ["*"]
    }
  }
  dynamic "statement" {
    for_each = local.render_on
    content {
      actions = ["batch:SubmitJob"]
      resources = [
        "${local.batch_arns}:job-queue/${var.render.job_queue}",
        "${local.batch_arns}:job-definition/${var.render.job_definition}",
        "${local.batch_arns}:job-definition/${var.render.job_definition}:*",
      ]
    }
  }
  dynamic "statement" {
    for_each = local.render_on
    content {
      actions   = ["batch:UpdateComputeEnvironment"]
      resources = ["${local.batch_arns}:compute-environment/${var.render.compute_environment}"]
    }
  }
  dynamic "statement" {
    for_each = local.render_on
    content {
      # shard progress only exists in the Batch log group
      actions   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
      resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:${var.render.log_group}:*"]
    }
  }

  # --- self-registration in DNS at boot, fenced to exactly one A record
  statement {
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${var.zone_id}"]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = [var.hostname]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsRecordTypes"
      values   = ["A"]
    }
  }
  statement {
    actions   = ["route53:GetChange"]
    resources = ["arn:aws:route53:::change/*"]
  }

  # --- secrets (OAuth client, session key, email allowlist) — created by hand, never in tf state
  statement {
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter/soundfont-explorer/admin/*"]
  }

  # --- songs.json cache bust after a publish
  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = ["arn:aws:cloudfront::${local.account_id}:distribution/${var.distribution_id}"]
  }

  # --- the UI's own shutdown button, fenced to this project's boxes
  statement {
    actions   = ["ec2:TerminateInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/project"
      values   = [local.tags.project]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
  tags = local.tags
}

# ---------------------------------------------------------------- launch template

# User-data is deliberately tiny: it writes a seed unit that fetches the *versioned*
# app/bootstrap.sh from the admin bucket and execs it. All real provisioning lives in that
# script (deployed by admin/scripts/deploy.sh), so it can change without touching Terraform.
resource "aws_launch_template" "admin" {
  name          = local.name
  description   = "Soundfont Explorer admin box: ephemeral, all state in the admin bucket"
  image_id      = data.aws_ami.debian.id
  instance_type = var.instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.instance.name
  }
  vpc_security_group_ids = [aws_security_group.admin.id]

  user_data = base64encode(templatefile("${path.module}/userdata.sh.tftpl", {
    bucket = aws_s3_bucket.admin.id
    region = local.region
  }))

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_gb
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
    }
  }

  metadata_options {
    http_tokens = "required"
  }

  # previews burst-render with fluidsynth; surplus vCPU-seconds are pennies and budget-capped
  credit_specification {
    cpu_credits = "unlimited"
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = local.name })
  }
  tag_specifications {
    resource_type = "volume"
    tags          = local.tags
  }

  tags = local.tags
}

# ---------------------------------------------------------------- budget (separate tag, separate budget)

resource "aws_budgets_budget" "admin" {
  tags         = local.tags
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:project$%s", local.tags.project)]
  }

  dynamic "notification" {
    for_each = [
      { threshold = 50, type = "ACTUAL" },
      { threshold = 80, type = "ACTUAL" },
      { threshold = 100, type = "FORECASTED" },
    ]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

output "bucket" { value = aws_s3_bucket.admin.id }
output "launch_template" { value = aws_launch_template.admin.name }
output "security_group_id" { value = aws_security_group.admin.id }
output "instance_role" { value = aws_iam_role.instance.name }
output "hostname" { value = var.hostname }
