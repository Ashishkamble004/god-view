variable "project_id" {
  type = string
}

variable "project_number" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "network_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "runtime_sa_email" {
  type = string
}

variable "runtime_sa_name" {
  type = string
}

variable "apigw_sa_email" {
  type = string
}

variable "sense_sa_email" {
  type = string
}

variable "act_sa_email" {
  type = string
}

variable "vm_machine_type" {
  type = string
}

variable "vm_disk_gb" {
  type = number
}

variable "cloud_run_cpu" {
  type = string
}

variable "cloud_run_memory" {
  type = string
}

variable "cloud_run_min_instances" {
  type = number
}

variable "cloud_run_max_instances" {
  type = number
}

variable "cloud_run_concurrency" {
  type = number
}

variable "hibernate" {
  type = bool
}

variable "images" {
  type = map(string)
}

variable "kyc_ilb_ip" {
  type = string
}

variable "los_ilb_ip" {
  type = string
}

variable "sql_private_ip" {
  type = string
}

variable "sql_database_name" {
  type = string
}

variable "pg_password_secret_id" {
  type = string
}

variable "kyc_vault_bucket" {
  type = string
}

variable "cdn_assets_bucket" {
  type = string
}

variable "ledger_dataset_id" {
  type = string
}

variable "sovereign_service_url" {
  type = string
}
