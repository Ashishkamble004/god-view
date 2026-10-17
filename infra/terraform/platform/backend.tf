terraform {
  required_version = ">= 1.9"
  backend "gcs" {
    bucket = "general-ak-god-view-tfstate"
    prefix = "platform"
  }
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
    random      = { source = "hashicorp/random", version = "~> 3.6" }
  }
}
