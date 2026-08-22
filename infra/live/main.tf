data "aws_caller_identity" "current" {}

module "dns_cert" {
  source    = "../modules/dns-cert"
  domain    = var.domain
  zone_name = var.zone_name
}

module "site" {
  source              = "../modules/static-site"
  bucket_name         = "soundfont-explorer-site-${data.aws_caller_identity.current.account_id}"
  domain              = var.domain
  acm_certificate_arn = module.dns_cert.certificate_arn
}

# A/AAAA alias records live here (not in dns-cert) so the module graph stays acyclic:
# dns-cert → site (cert arn) → records (distribution domain).
resource "aws_route53_record" "a" {
  zone_id = module.dns_cert.zone_id
  name    = var.domain
  type    = "A"
  alias {
    name                   = module.site.distribution_domain_name
    zone_id                = module.site.distribution_hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "aaaa" {
  zone_id = module.dns_cert.zone_id
  name    = var.domain
  type    = "AAAA"
  alias {
    name                   = module.site.distribution_domain_name
    zone_id                = module.site.distribution_hosted_zone_id
    evaluate_target_health = false
  }
}

module "budget" {
  source      = "../modules/budget"
  limit_usd   = var.budget_limit_usd
  alert_email = var.alert_email
}

module "circuit_breaker" {
  source          = "../modules/circuit-breaker"
  distribution_id = module.site.distribution_id
  daily_gb        = var.egress_daily_gb
  monthly_gb      = var.egress_monthly_gb
  alert_email     = var.alert_email
}

# Off by default: creating it costs ~$0.46/mo (the font bucket) and nothing else until a run
# is submitted, but it is real compute infrastructure and should be a deliberate choice.
module "render_fleet" {
  source      = "../modules/render-fleet"
  count       = var.enable_render_fleet ? 1 : 0
  providers   = { aws = aws.render }
  alert_email = var.alert_email
  site_bucket = module.site.bucket_name

  max_vcpus            = var.render_max_vcpus
  max_instance_minutes = var.render_max_instance_minutes
  budget_limit_usd     = var.render_budget_limit_usd
}

module "phase2" {
  source = "../modules/phase2"
  count  = var.enable_phase2 ? 1 : 0
  domain = var.domain
}
