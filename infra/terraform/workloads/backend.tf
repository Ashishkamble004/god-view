terraform {
  required_version = ">= 1.9"

  backend "gcs" {
    bucket = "general-ak-god-view-tfstate"
    prefix = "workloads"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}
