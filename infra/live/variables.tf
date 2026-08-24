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

variable "enable_render_fleet" {
  type        = bool
  default     = false
  description = "Create the burst render fleet (AWS Batch on spot). Idle cost is the font bucket alone."
}

variable "render_max_vcpus" {
  type        = number
  default     = 2304
  description = "Hard ceiling on concurrent fleet vCPUs (12 x c7a.48xlarge)."
}

variable "render_max_instance_minutes" {
  type        = number
  default     = 240
  description = "Watchdog terminates any fleet instance older than this, unconditionally."
}

variable "render_budget_limit_usd" {
  type        = number
  default     = 100
  description = "Monthly budget for project=soundfont-explorer-render, separate from the site budget."
}

variable "enable_admin" {
  type        = bool
  default     = false
  description = "Create the admin-box durables (bucket, IAM, launch template). The instance itself is launched by admin/scripts/up.sh; idle cost is the bucket alone."
}

variable "admin_budget_limit_usd" {
  type        = number
  default     = 5
  description = "Monthly budget for project=soundfont-explorer-admin, separate from site and render."
}
