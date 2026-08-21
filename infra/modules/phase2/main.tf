# Phase 2 skeleton (plan §12): Lambda Function URLs + DynamoDB + SQS + SES behind /api/*.
# Instantiated with count = var.enable_phase2 ? 1 : 0 (default off). Only the pieces that are
# free at rest and needed to validate the design are declared.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "domain" {
  type = string
}

variable "upload_expiry_days" {
  type    = number
  default = 60
}

resource "aws_dynamodb_table" "main" {
  name         = "soundfont-explorer-phase2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_sqs_queue" "render_dlq" {
  name                      = "soundfont-explorer-render-jobs-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "render" {
  name                       = "soundfont-explorer-render-jobs"
  visibility_timeout_seconds = 7200
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.render_dlq.arn
    maxReceiveCount     = 3
  })
}

output "queue_url" {
  value = aws_sqs_queue.render.url
}

output "table_name" {
  value = aws_dynamodb_table.main.name
}
