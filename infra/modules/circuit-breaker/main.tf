terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

variable "distribution_id" {
  type = string
}

variable "daily_gb" {
  type = number
}

variable "monthly_gb" {
  type = number
}

variable "alert_email" {
  type = string
}

locals {
  name = "soundfont-explorer-circuit-breaker"
}

resource "aws_sns_topic" "alerts" {
  name = local.name
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Failed Lambda invocations (trip could not disable the distribution, metric query failed,
# init error, ...) are mailed from here instead of being retried twice and dropped. A separate
# topic on purpose: the alerts topic also feeds the Lambda, so routing its own failure records
# back into it would loop if the function failed at init. Needs a one-time e-mail confirmation.
resource "aws_sns_topic" "failures" {
  name = "${local.name}-failures"
}

resource "aws_sns_topic_subscription" "failures_email" {
  topic_arn = aws_sns_topic.failures.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------- daily alarm (CloudFront metrics live in us-east-1)

resource "aws_cloudwatch_metric_alarm" "daily_egress" {
  alarm_name          = "${local.name}-daily-egress"
  alarm_description   = "CloudFront BytesDownloaded over 24 h above ${var.daily_gb} GB"
  namespace           = "AWS/CloudFront"
  metric_name         = "BytesDownloaded"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.daily_gb * 1073741824
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    DistributionId = var.distribution_id
    Region         = "Global"
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------- lambda

data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/lambda/handler.py"
  output_path = "${path.module}/lambda/handler.zip"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    actions   = ["cloudfront:GetDistributionConfig", "cloudfront:UpdateDistribution"]
    resources = ["arn:aws:cloudfront::*:distribution/${var.distribution_id}"]
  }
  statement {
    actions   = ["cloudwatch:GetMetricStatistics", "cloudwatch:GetMetricData"]
    resources = ["*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn, aws_sns_topic.failures.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "breaker" {
  function_name    = local.name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 30
  memory_size      = 128
  environment {
    variables = {
      DISTRIBUTION_ID = var.distribution_id
      TOPIC_ARN       = aws_sns_topic.alerts.arn
      DAILY_GB        = tostring(var.daily_gb)
      MONTHLY_GB      = tostring(var.monthly_gb)
    }
  }
  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]
}

# On-failure destination: both triggers invoke the function asynchronously, where an error is
# retried twice and then dropped. The failure record (request + error) is mailed instead.
resource "aws_lambda_function_event_invoke_config" "breaker" {
  function_name = aws_lambda_function.breaker.function_name
  destination_config {
    on_failure {
      destination = aws_sns_topic.failures.arn
    }
  }
}

# alarm → SNS → lambda
resource "aws_sns_topic_subscription" "lambda" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.breaker.arn
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.breaker.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

# hourly month-to-date check
resource "aws_cloudwatch_event_rule" "hourly" {
  name                = "${local.name}-hourly"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "hourly" {
  rule = aws_cloudwatch_event_rule.hourly.name
  arn  = aws_lambda_function.breaker.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.breaker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hourly.arn
}

output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "alarm_name" {
  value = aws_cloudwatch_metric_alarm.daily_egress.alarm_name
}
