# Burst render fleet: AWS Batch array jobs on EC2 Spot, scale 0 -> N -> 0.
#
# Cost shape: idle is $0 (a managed compute environment with no instances) plus the font
# bucket (~46 GiB One Zone-IA, ~$0.46/mo). Everything expensive only exists while a run is
# in flight, and three things make sure that stays true:
#   - the compute environment ships DISABLED; submit.py enables it and disables it again
#   - max_vcpus is a hard ceiling on how much can ever run at once
#   - a watchdog Lambda terminates over-age instances and stuck jobs (lambda/handler.py)
#
# This account is shared with unrelated projects, so every permission that can destroy
# something is scoped by the project tag.

terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0", configuration_aliases = [aws] }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

variable "alert_email" { type = string }
variable "site_bucket" { type = string }

variable "max_vcpus" {
  type        = number
  default     = 2304
  description = "Hard ceiling on concurrent vCPUs (12 x c7a.48xlarge). The main cost stop."
}

variable "instance_types" {
  type    = list(string)
  default = ["c7a.48xlarge", "c7a.24xlarge", "c7i.48xlarge", "c7i.24xlarge"]
}

variable "max_instance_minutes" {
  type        = number
  default     = 240
  description = "Watchdog terminates any fleet instance older than this, unconditionally."
}

variable "max_job_minutes" {
  type        = number
  default     = 60
  description = "Watchdog terminates any job RUNNING longer than this (longest real job: 402 s)."
}

variable "budget_limit_usd" {
  type        = number
  default     = 100
  description = "Monthly budget for project=soundfont-explorer-render, separate from the site's."
}

variable "root_volume_gb" {
  type        = number
  default     = 300
  description = "Fonts are 46 GiB and per-song outputs land beside them; 300 leaves headroom."
}

locals {
  name = "soundfont-explorer-render"
  tags = { project = "soundfont-explorer-render" }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "fleet" {
  name        = local.name
  description = "Soundfont Explorer render fleet: egress only"
  vpc_id      = data.aws_vpc.default.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = local.name })
}

# ---------------------------------------------------------------- fonts + image

resource "aws_s3_bucket" "fonts" {
  bucket = "${local.name}-fonts-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "fonts" {
  bucket                  = aws_s3_bucket.fonts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fonts" {
  bucket = aws_s3_bucket.fonts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# SF2s are re-uploadable from bimmer, so single-AZ durability is the right trade.
resource "aws_s3_bucket_lifecycle_configuration" "fonts" {
  bucket = aws_s3_bucket.fonts.id
  rule {
    id     = "onezone-ia"
    status = "Enabled"
    filter {}
    transition {
      days          = 30
      storage_class = "ONEZONE_IA"
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

resource "aws_ecr_repository" "sfr" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = false
  }
  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "sfr" {
  repository = aws_ecr_repository.sfr.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 5 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
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

resource "aws_iam_role_policy_attachment" "instance_ecs" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
  tags = local.tags
}

data "aws_iam_policy_document" "assume_ecs_task" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# what the render container itself may touch: read fonts, write published objects
resource "aws_iam_role" "job" {
  name               = "${local.name}-job"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_task.json
  tags               = local.tags
}

data "aws_iam_policy_document" "job" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.fonts.arn, "${aws_s3_bucket.fonts.arn}/*"]
  }
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.site_bucket}", "arn:aws:s3:::${var.site_bucket}/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_task.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ---------------------------------------------------------------- compute

resource "aws_launch_template" "fleet" {
  name        = local.name
  description = "Soundfont Explorer render fleet: bigger root volume for fonts + outputs"
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_gb
      volume_type           = "gp3"
      throughput            = 500
      iops                  = 6000
      delete_on_termination = true
      encrypted             = true
    }
  }
  # every instance carries the tag the watchdog and the budget filter on
  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = local.name })
  }
  tag_specifications {
    resource_type = "volume"
    tags          = local.tags
  }
  tag_specifications {
    resource_type = "network-interface"
    tags          = local.tags
  }
  tag_specifications {
    resource_type = "spot-instances-request"
    tags          = local.tags
  }
  tags = local.tags
}

resource "aws_batch_compute_environment" "fleet" {
  name = local.name
  type = "MANAGED"
  # AWS refuses to CREATE a compute environment in DISABLED state ("Compute Environment must be
  # created in ENABLED state"), so it is created enabled and put to sleep immediately afterwards
  # by terraform_data.disable_ce below. The deployed steady state is DISABLED: submit.py enables
  # it for a run and disables it again, and the watchdog disables it on any trip.
  # Idle cost is nil either way — min_vcpus = 0 means no instance exists until a job needs one.
  state        = "ENABLED"
  service_role = null

  compute_resources {
    type                = "SPOT"
    allocation_strategy = "SPOT_CAPACITY_OPTIMIZED"
    max_vcpus           = var.max_vcpus
    min_vcpus           = 0
    desired_vcpus       = 0
    instance_type       = var.instance_types
    instance_role       = aws_iam_instance_profile.instance.arn
    subnets             = data.aws_subnets.default.ids
    security_group_ids  = [aws_security_group.fleet.id]
    tags                = local.tags

    launch_template {
      launch_template_id = aws_launch_template.fleet.id
      version            = "$Latest"
    }
  }

  tags = local.tags

  lifecycle {
    # submit.py and the watchdog both flip state; Terraform must not fight them.
    ignore_changes = [state]
  }
}

# Put the freshly created environment to sleep. A separate resource rather than a provisioner on
# the compute environment itself, so a failure here cannot taint (and thus schedule for
# replacement) the environment.
resource "terraform_data" "disable_ce" {
  input = aws_batch_compute_environment.fleet.name
  provisioner "local-exec" {
    command = "aws batch update-compute-environment --compute-environment ${self.input} --state DISABLED >/dev/null"
  }
}

resource "aws_batch_job_queue" "fleet" {
  name     = local.name
  state    = "ENABLED"
  priority = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.fleet.arn
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "jobs" {
  name              = "/aws/batch/${local.name}"
  retention_in_days = 14
  tags              = local.tags
}

resource "aws_batch_job_definition" "shard" {
  name                  = local.name
  type                  = "container"
  platform_capabilities = ["EC2"]
  propagate_tags        = true

  # A stuck job cannot outlive this even if the watchdog is broken.
  timeout {
    attempt_duration_seconds = var.max_job_minutes * 60
  }
  # Spot reclaim is retried; a job that fails on its own merits is not retried forever.
  retry_strategy {
    attempts = 3
    evaluate_on_exit {
      action           = "RETRY"
      on_status_reason = "Host EC2*"
    }
    evaluate_on_exit {
      action    = "EXIT"
      on_reason = "*"
    }
  }

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.sfr.repository_url}:latest"
    jobRoleArn       = aws_iam_role.job.arn
    executionRoleArn = aws_iam_role.execution.arn
    command          = ["python3", "/opt/cloud/shard.py"]
    resourceRequirements = [
      { type = "VCPU", value = "180" },
      { type = "MEMORY", value = "350000" },
    ]
    environment = [
      { name = "SFR_FONTS_BUCKET", value = aws_s3_bucket.fonts.id },
      { name = "SFR_SITE_BUCKET", value = var.site_bucket },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.jobs.name }
    }
  })

  tags = local.tags
}

# ---------------------------------------------------------------- watchdog

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

data "archive_file" "watchdog" {
  type        = "zip"
  source_file = "${path.module}/lambda/handler.py"
  output_path = "${path.module}/lambda/handler.zip"
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "watchdog" {
  name               = "${local.name}-watchdog"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
  tags               = local.tags
}

data "aws_iam_policy_document" "watchdog" {
  # read-only discovery
  statement {
    actions = ["ec2:DescribeInstances", "batch:ListJobs", "batch:DescribeJobs",
    "batch:DescribeComputeEnvironments"]
    resources = ["*"]
  }
  # destructive, and deliberately fenced to this project's own instances: the account is
  # shared, and a watchdog that could terminate someone else's box is worse than no watchdog.
  statement {
    actions   = ["ec2:TerminateInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/project"
      values   = ["soundfont-explorer-render"]
    }
  }
  statement {
    actions   = ["batch:TerminateJob"]
    resources = ["*"]
  }
  statement {
    actions   = ["batch:UpdateComputeEnvironment"]
    resources = [aws_batch_compute_environment.fleet.arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.watchdog.arn}:*"]
  }
}

resource "aws_iam_role_policy" "watchdog" {
  role   = aws_iam_role.watchdog.id
  policy = data.aws_iam_policy_document.watchdog.json
}

resource "aws_cloudwatch_log_group" "watchdog" {
  name              = "/aws/lambda/${local.name}-watchdog"
  retention_in_days = 14
  tags              = local.tags
}

resource "aws_lambda_function" "watchdog" {
  function_name    = "${local.name}-watchdog"
  role             = aws_iam_role.watchdog.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.watchdog.output_path
  source_code_hash = data.archive_file.watchdog.output_base64sha256
  timeout          = 60
  memory_size      = 128
  environment {
    variables = {
      COMPUTE_ENV          = aws_batch_compute_environment.fleet.name
      JOB_QUEUE            = aws_batch_job_queue.fleet.name
      TOPIC_ARN            = aws_sns_topic.alerts.arn
      PROJECT_TAG          = "soundfont-explorer-render"
      MAX_INSTANCE_MINUTES = tostring(var.max_instance_minutes)
      MAX_JOB_MINUTES      = tostring(var.max_job_minutes)
    }
  }
  tags       = local.tags
  depends_on = [aws_cloudwatch_log_group.watchdog, aws_iam_role_policy.watchdog]
}

resource "aws_cloudwatch_event_rule" "watchdog" {
  name                = "${local.name}-watchdog"
  description         = "Soundfont Explorer render fleet watchdog"
  schedule_expression = "rate(5 minutes)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "watchdog" {
  rule      = aws_cloudwatch_event_rule.watchdog.name
  target_id = "lambda"
  arn       = aws_lambda_function.watchdog.arn
}

resource "aws_lambda_permission" "watchdog_events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.watchdog.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.watchdog.arn
}

# ---------------------------------------------------------------- budget (separate from the site's)

resource "aws_budgets_budget" "render" {
  tags         = local.tags
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$soundfont-explorer-render"]
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = "ACTUAL"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
      subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    }
  }
}

output "fonts_bucket" { value = aws_s3_bucket.fonts.id }
output "ecr_repository_url" { value = aws_ecr_repository.sfr.repository_url }
output "job_queue" { value = aws_batch_job_queue.fleet.name }
output "job_definition" { value = aws_batch_job_definition.shard.name }
output "compute_environment" { value = aws_batch_compute_environment.fleet.name }
output "alerts_topic_arn" { value = aws_sns_topic.alerts.arn }
