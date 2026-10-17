variable "project_id" {
  type = string
}

variable "project_number" {
  type = string
}

variable "region" {
  type = string
}

variable "network_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "sense_sa_email" {
  type = string
}

variable "act_sa_email" {
  type = string
}

variable "runtime_sa_email" {
  type = string
}

variable "operator_email" {
  type = string
}

variable "billing_account_id" {
  type = string
}

variable "create_budget" {
  type = bool
}

variable "sovereign_cpu" {
  type    = string
  default = "2"
}

variable "sovereign_memory" {
  type    = string
  default = "2Gi"
}

variable "sovereign_min_instances" {
  type    = number
  default = 1
}

variable "sovereign_max_instances" {
  type    = number
  default = 1
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

variable "cockpit_max_instances" {
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

variable "firestore_db_name" {
  type = string
}

variable "ledger_dataset_id" {
  type = string
}

variable "agent_skills_bucket" {
  type = string
}

variable "agent_staging_bucket" {
  type = string
}

variable "tfstate_bucket" {
  type = string
}

variable "simulator_service_url" {
  type = string
}

variable "godview_court_max_agents" {
  type = number
}

variable "godview_hint_exclude" {
  type = string
}

variable "token_budget_daily_application_usd" {
  type = number
}

variable "token_budget_daily_business_line_usd" {
  type = number
}

variable "token_budget_daily_change_usd" {
  type = number
}

variable "token_budget_daily_data_usd" {
  type = number
}

variable "token_budget_daily_platform_usd" {
  type = number
}

variable "token_budget_daily_sovereign_usd" {
  type = number
}

variable "token_budget_daily_fleet_ceiling_usd" {
  type = number
}

variable "token_budget_argon_reserve_pct" {
  type = number
}

variable "sql_private_ip" {
  type = string
}

variable "sql_readonly_password" {
  type      = string
  sensitive = true
}
