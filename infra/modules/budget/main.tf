terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "limit_usd" {
  type = number
}

variable "alert_email" {
  type = string
}

variable "project_tag" {
  type    = string
  default = "soundfont-explorer"
}

# Costs are attributed to this project through the `project` tag that the provider's default_tags
# puts on every resource. The tag must be an *active* cost-allocation tag before Budgets / Cost
# Explorer can filter on it (activation is not retroactive and can take up to 24 h to show data).
resource "aws_ce_cost_allocation_tag" "project" {
  tag_key = "project"
  status  = "Active"
}

resource "aws_budgets_budget" "monthly" {
  name         = "soundfont-explorer-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # scope: only resources tagged project=<project_tag> — never the rest of the account
  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:project$%s", var.project_tag)]
  }

  depends_on = [aws_ce_cost_allocation_tag.project]

  # 50 % and 80 % of actual spend, 100 % of the forecast
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

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}
