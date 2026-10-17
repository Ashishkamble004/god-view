variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "runtime_sa_email" {
  type = string
}

variable "operator_email" {
  type    = string
  default = "admin@kambleashish.altostrat.com"
}

variable "dns_zone_name" {
  type    = string
  default = "ak-demos-com"
}

variable "lookup_dns_zone" {
  type    = bool
  default = true
}

variable "cdn_assets_bucket_name" {
  type = string
}

variable "portal_web_service_name" {
  type = string
}

variable "los_web_service_name" {
  type = string
}

variable "api_gateway_id" {
  type = string
}

variable "sovereign_service_name" {
  type = string
}

variable "sre_web_service_name" {
  type = string
}
