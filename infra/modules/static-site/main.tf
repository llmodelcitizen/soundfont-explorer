terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "bucket_name" {
  type = string
}

variable "domain" {
  type = string
}

variable "acm_certificate_arn" {
  type = string
}

# ---------------------------------------------------------------- bucket (private, OAC only)

resource "aws_s3_bucket" "site" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid       = "AllowCloudFrontServicePrincipalReadOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.bucket.json
}

# ---------------------------------------------------------------- CloudFront

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.bucket_name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# AWS managed "CachingOptimized" (gzip/brotli aware, no query strings / cookies / headers in the key)
locals {
  caching_optimized_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  csp = join("; ", [
    "default-src 'self'",
    "script-src 'self' 'wasm-unsafe-eval'",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "media-src 'self' blob:",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "base-uri 'self'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ])
}

resource "aws_cloudfront_response_headers_policy" "site" {
  name = "${var.bucket_name}-headers"
  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = false
      preload                    = false
      override                   = true
    }
    content_type_options {
      override = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    content_security_policy {
      content_security_policy = local.csp
      override                = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
  }
  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=()"
      override = true
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = var.domain
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"
  aliases             = [var.domain]
  # NOTE: never set a flat-rate plan attribute here — pay-as-you-go only (plan D5).

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = local.caching_optimized_policy_id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
    # no origin-request policy: same-origin site, Range requests pass through to S3
  }

  # S3 answers a missing key with 403 (no ListBucket), so both codes map to the 404 page.
  dynamic "custom_error_response" {
    for_each = [403, 404]
    content {
      error_code            = custom_error_response.value
      response_code         = 404
      response_page_path    = "/404.html"
      error_caching_min_ttl = 60
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.acm_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  # The circuit breaker (modules/circuit-breaker) disables the distribution out of band. Ignore
  # that drift so a routine `terraform apply` cannot silently reopen the site; re-enabling is the
  # explicit manual step in docs/DEPLOY.md "If the circuit breaker trips".
  lifecycle {
    ignore_changes = [enabled]
  }
}

output "bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.site.arn
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "distribution_arn" {
  value = aws_cloudfront_distribution.site.arn
}

output "distribution_domain_name" {
  value = aws_cloudfront_distribution.site.domain_name
}

output "distribution_hosted_zone_id" {
  value = aws_cloudfront_distribution.site.hosted_zone_id
}
