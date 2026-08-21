variable "domain" {
  type    = string
  default = "soundfonts.ericq.com"
}

variable "zone_name" {
  type    = string
  default = "ericq.com."
}

variable "alert_email" {
  type        = string
  description = "Owner email for budget, circuit-breaker and SNS notifications (set in terraform.tfvars, not committed)"
}

variable "budget_limit_usd" {
  type    = number
  default = 10
}

variable "egress_daily_gb" {
  type    = number
  default = 150
}

variable "egress_monthly_gb" {
  type    = number
  default = 900
}

variable "enable_phase2" {
  type    = bool
  default = false
}
