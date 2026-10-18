terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # State stays local for now (gitignored). Move to an S3 backend before CI runs Terraform.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project = "vehicle-safety-pipeline"
    }
  }
}

data "aws_caller_identity" "current" {}
