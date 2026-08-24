terraform {
  required_version = ">= 1.11"
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
  backend "s3" {
    bucket       = "soundfont-explorer-tfstate-977521774238"
    key          = "live.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = {
      project = "soundfont-explorer"
      managed = "terraform"
    }
  }
}

# The burst render fleet bills to its own project tag, so its spend never lands in the site's
# $10 budget and the watchdog's terminate permission can be fenced to it. default_tags makes
# that structural: a resource added to the module later is tagged whether or not anyone
# remembers to. It does NOT reach instances launched by Batch — those are tagged explicitly
# through the launch template's tag_specifications and compute_resources.tags.
provider "aws" {
  alias  = "render"
  region = "us-east-1"
  default_tags {
    tags = {
      project = "soundfont-explorer-render"
      managed = "terraform"
    }
  }
}

# The admin box gets the same treatment for the same reasons: its own cost tag (own $5
# budget, out of the site's $10) and, critically, NOT the render tag — the fleet watchdog
# unconditionally terminates project=soundfont-explorer-render instances older than 4 h.
provider "aws" {
  alias  = "admin"
  region = "us-east-1"
  default_tags {
    tags = {
      project = "soundfont-explorer-admin"
      managed = "terraform"
    }
  }
}
