# Bootstrap: the Terraform state backend itself.
#
# WHY THIS IS A SEPARATE ROOT CONFIGURATION
# The main configuration keeps its state in an S3 bucket. Something has to
# create that bucket, and it cannot be the configuration whose state lives in
# it. That chicken-and-egg is inherent to Terraform, not a quirk here: this
# root runs ONCE, with local state, and its own state file is unimportant
# afterwards — losing it costs you an `import`, not the bucket.
#
# WHY IT MATTERS MORE THAN IT LOOKS
# Without remote state, .github/workflows/deploy.yml would start every run on
# a fresh GitHub runner with an EMPTY state file and try to create the whole
# stack again — duplicate Lambdas, a bucket name already taken, an apply that
# fails halfway. Remote state is what makes the deploy pipeline work at all,
# not a nicety for teams.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "project_name" {
  type    = string
  default = "aiops-traffic-shaper"
}

variable "environment" {
  type    = string
  default = "prod"
}

module "state_backend" {
  source       = "../modules/s3-backend"
  project_name = var.project_name
  environment  = var.environment

  # No DynamoDB lock table. Terraform 1.10 added S3-native locking, which the
  # main configuration uses via `use_lockfile = true`. The lock table was
  # PAY_PER_REQUEST, which ADR-002 is explicit about: on-demand billing has no
  # Always-Free allowance at all. Pennies, but pennies this project does not
  # have to spend.
  create_lock_table = false
}

output "state_bucket" {
  description = "Must match the bucket name hardcoded in ../backend.tf."
  value       = module.state_backend.bucket_name
}
