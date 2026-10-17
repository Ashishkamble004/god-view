variable "project_id" {
  description = "Target GCP project ID"
  type        = string
  default     = "general-ak"
}

variable "project_number" {
  description = "Target GCP project number"
  type        = string
  default     = "769002985772"
}

variable "region" {
  description = "Primary GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Primary GCP zone (single-zone, no HA)"
  type        = string
  default     = "us-central1-a"
}

variable "github_repository" {
  description = "GitHub repository owner/name for Workload Identity Federation"
  type        = string
  default     = "kambleashish/god-view"
}

variable "cloud_run_cpu" {
  description = "CPU limit for tenant and cockpit Cloud Run services"
  type        = string
  default     = "1"
}

variable "cloud_run_memory" {
  description = "Memory limit for tenant and cockpit Cloud Run services"
  type        = string
  default     = "512Mi"
}

variable "cloud_run_min_instances" {
  description = "Min instance count for tenant and cockpit Cloud Run services"
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Max instance count for tenant Cloud Run services"
  type        = number
  default     = 3
}

variable "cockpit_max_instances" {
  description = "Max instance count for God-View Cockpit Cloud Run service (A7)"
  type        = number
  default     = 2
}

variable "cloud_run_concurrency" {
  description = "Max concurrent requests per Cloud Run instance"
  type        = number
  default     = 80
}

variable "vm_machine_type" {
  description = "Machine type for core-banking-cibil-vm"
  type        = string
  default     = "e2-micro"
}

variable "vm_disk_gb" {
  description = "Boot disk size in GB for core-banking-cibil-vm"
  type        = number
  default     = 10
}

variable "sql_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-g1-small"
}

variable "sql_disk_gb" {
  description = "Cloud SQL disk size in GB"
  type        = number
  default     = 10
}

variable "sql_availability_type" {
  description = "Cloud SQL availability type (ZONAL single-zone)"
  type        = string
  default     = "ZONAL"
}

variable "sovereign_cpu" {
  description = "CPU allocation for god-view-sovereign"
  type        = string
  default     = "2"
}

variable "sovereign_min_instances" {
  description = "Min instances for god-view-sovereign (always-on)"
  type        = number
  default     = 1
}

variable "sovereign_max_instances" {
  description = "Max instances for god-view-sovereign (single Sentinel Engine)"
  type        = number
  default     = 1
}

variable "sovereign_memory" {
  description = "Memory allocation for god-view-sovereign (A7)"
  type        = string
  default     = "2Gi"
}

variable "traffic_resume_cron" {
  description = "Cron schedule for resuming tenant traffic at 09:00 IST"
  type        = string
  default     = "0 9 * * *"
}

variable "traffic_pause_cron" {
  description = "Cron schedule for pausing tenant traffic at 17:00 IST"
  type        = string
  default     = "0 17 * * *"
}

variable "simulator_tick_cron" {
  description = "Cron schedule for AI customer simulator per-minute tick (09:00-16:59 IST)"
  type        = string
  default     = "* 9-16 * * *"
}

variable "probe_run_cron" {
  description = "Cron schedule for probe runner (every 5 min inside 09:00-17:00 IST)"
  type        = string
  default     = "*/5 9-16 * * *"
}

variable "agent_reflection_cron" {
  description = "Cron schedule for nightly agent reflection at 03:30 IST"
  type        = string
  default     = "30 3 * * *"
}

variable "scheduler_time_zone" {
  description = "Time zone for all Cloud Scheduler jobs"
  type        = string
  default     = "Asia/Kolkata"
}

variable "traffic_weekdays_only" {
  description = "If true, restrict scheduled resume and simulator ticks to Monday-Friday"
  type        = bool
  default     = false
}

variable "hibernate" {
  description = "Hibernate mode: scales infra to cost floor"
  type        = bool
  default     = false
}

variable "kill_switch_armor_enforce" {
  description = "When true, Cloud Armor priority-100 deny rule is enforced (not preview)"
  type        = bool
  default     = false
}

variable "kill_switch_allowlist_cidrs" {
  description = "CIDRs exempted from Cloud Armor kill-switch deny rule"
  type        = list(string)
  default     = []
}

variable "armor_rate_limit_per_minute" {
  description = "Cloud Armor per-IP rate limit requests per minute (A6)"
  type        = number
  default     = 120
}

variable "armor_ban_duration_sec" {
  description = "Cloud Armor rate-limit ban duration in seconds (A6)"
  type        = number
  default     = 300
}

variable "images" {
  description = "Container image map pinned by CI into images.auto.tfvars"
  type        = map(string)
  default = {
    "loan-orchestrator-api"       = "us-docker.pkg.dev/cloudrun/container/hello"
    "commercial-fleet-credit-svc" = "us-docker.pkg.dev/cloudrun/container/hello"
    "motor-insurance-svc"         = "us-docker.pkg.dev/cloudrun/container/hello"
    "customer-loan-portal-web"    = "us-docker.pkg.dev/cloudrun/container/hello"
    "internal-bank-los-web"       = "us-docker.pkg.dev/cloudrun/container/hello"
    "ai-customer-simulator"       = "us-docker.pkg.dev/cloudrun/container/hello"
    "god-view-sovereign"          = "us-docker.pkg.dev/cloudrun/container/hello"
    "god-view-sre-web"            = "us-docker.pkg.dev/cloudrun/container/hello"
    "god-view-hibernate-job"      = "us-docker.pkg.dev/cloudrun/container/hello"
    "core-banking-cibil-vm"       = "us-docker.pkg.dev/cloudrun/container/hello"
  }
}

variable "enable_datastream" {
  description = "Enable Phase 2 Datastream CDC resources (A13)"
  type        = bool
  default     = false
}

variable "create_budget" {
  description = "Whether to create the google_billing_budget resource"
  type        = bool
  default     = true
}

variable "billing_account_id" {
  description = "Billing account ID for google_billing_budget"
  type        = string
  default     = "019575-4F86FB-B4652B"
}

variable "budget_amount_usd" {
  description = "Monthly budget ceiling in USD"
  type        = number
  default     = 600
}

variable "budget_thresholds" {
  description = "Budget alert threshold fractions"
  type        = list(number)
  default     = [0.5, 0.8, 1.0]
}

variable "iap_allowed_members" {
  description = "IAM members granted roles/iap.httpsResourceAccessor"
  type        = list(string)
  default     = ["user:admin@kambleashish.altostrat.com"]
}

variable "alert_email_address" {
  description = "Ops email address for Monitoring notification channel"
  type        = string
  default     = "admin@kambleashish.altostrat.com"
}

variable "operator_email" {
  description = "Operator email address for IAP and Monitoring alerts"
  type        = string
  default     = "admin@kambleashish.altostrat.com"
}

variable "dns_zone_name" {
  description = "Existing Cloud DNS managed zone name in general-ak"
  type        = string
  default     = "ak-demos-com"
}

variable "lookup_dns_zone" {
  description = "Whether to query data.google_dns_managed_zone during plan (true in GCP, false in offline hermetic tests)"
  type        = bool
  default     = true
}

variable "enable_chat_notification_channel" {
  description = "Optional Google Chat notification channel flag (A4/A15)"
  type        = bool
  default     = false
}

variable "godview_court_max_agents" {
  description = "Hard cap on Court Reasoning Engine agents (A1)"
  type        = number
  default     = 40
}

variable "godview_hint_exclude" {
  description = "Comma-separated resource names excluded from tenant discovery (A11)"
  type        = string
  default     = "general-ak-god-view-tfstate,god-view-sovereign,god-view-sre-web,general-ak-god-view-agent-staging"
}

variable "token_budget_daily_application_usd" {
  description = "Daily token budget in USD per Application Agent (A9)"
  type        = number
  default     = 0.75
}

variable "token_budget_daily_business_line_usd" {
  description = "Daily token budget in USD per Business-Line Agent (A9)"
  type        = number
  default     = 0.75
}

variable "token_budget_daily_change_usd" {
  description = "Daily token budget in USD per Change Agent (A9)"
  type        = number
  default     = 0.75
}

variable "token_budget_daily_data_usd" {
  description = "Daily token budget in USD per Data Agent (A9)"
  type        = number
  default     = 0.50
}

variable "token_budget_daily_platform_usd" {
  description = "Daily token budget in USD per Platform Agent (A9)"
  type        = number
  default     = 0.50
}

variable "token_budget_daily_sovereign_usd" {
  description = "Daily token budget in USD for the Sovereign (A9)"
  type        = number
  default     = 1.50
}

variable "token_budget_daily_fleet_ceiling_usd" {
  description = "Daily token budget ceiling in USD across the entire fleet (A9)"
  type        = number
  default     = 6.00
}

variable "token_budget_argon_reserve_pct" {
  description = "Fraction of fleet ceiling reserved for gemini-4-argon escalation (A9)"
  type        = number
  default     = 0.20
}
