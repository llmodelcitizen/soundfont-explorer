output "bucket" {
  value = module.site.bucket_name
}

output "distribution_id" {
  value = module.site.distribution_id
}

output "distribution_domain_name" {
  value = module.site.distribution_domain_name
}

output "site_url" {
  value = "https://${var.domain}/"
}

output "circuit_breaker_topic" {
  value = module.circuit_breaker.topic_arn
}
