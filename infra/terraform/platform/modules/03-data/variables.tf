variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "network_id" {
  type = string
}

variable "psa_connection_id" {
  type = string
}

variable "sql_tier" {
  type = string
}

variable "sql_disk_gb" {
  type = number
}

variable "sql_availability_type" {
  type = string
}

variable "hibernate" {
  type = bool
}

variable "enable_datastream" {
  type = bool
}

variable "sense_sa_email" {
  type = string
}

variable "runtime_sa_email" {
  type = string
}

variable "ledger_table_deletion_protection" {
  description = "Enable deletion protection on BigQuery ledger tables"
  type        = bool
  default     = false
}
