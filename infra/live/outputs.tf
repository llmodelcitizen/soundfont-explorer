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

output "render_fleet" {
  description = "Empty unless enable_render_fleet; read by render/cloud/submit.py."
  value = var.enable_render_fleet ? {
    fonts_bucket        = module.render_fleet[0].fonts_bucket
    ecr_repository_url  = module.render_fleet[0].ecr_repository_url
    job_queue           = module.render_fleet[0].job_queue
    job_definition      = module.render_fleet[0].job_definition
    compute_environment = module.render_fleet[0].compute_environment
    alerts_topic_arn    = module.render_fleet[0].alerts_topic_arn
    log_group           = module.render_fleet[0].log_group
  } : null
}

output "admin" {
  description = "Empty unless enable_admin; read by admin/scripts/{up,down,deploy}.sh."
  value = var.enable_admin ? {
    bucket          = module.admin[0].bucket
    launch_template = module.admin[0].launch_template
    hostname        = module.admin[0].hostname
    url             = "https://${module.admin[0].hostname}/"
  } : null
}
