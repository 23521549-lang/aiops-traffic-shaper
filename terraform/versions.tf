terraform {
  required_version = ">= 1.10.0" # use_lockfile in backend.tf needs 1.10

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
