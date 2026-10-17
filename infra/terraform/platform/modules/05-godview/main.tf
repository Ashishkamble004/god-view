locals {
  godview_labels = {
    godview_managed = "true"
    tf_module       = "05-godview"
    tenant          = "godview"
    godview_role    = "observer"
  }

  sovereign_invokers = toset([
    "serviceAccount:${var.sense_sa_email}",
    "serviceAccount:${var.act_sa_email}",
    "serviceAccount:${var.runtime_sa_email}",
    "serviceAccount:service-${var.project_number}@gcp-sa-iap.iam.gserviceaccount.com",
    "serviceAccount:service-${var.project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com",
  ])

  cockpit_invokers = toset([
    "serviceAccount:service-${var.project_number}@gcp-sa-iap.iam.gserviceaccount.com",
    "serviceAccount:${var.sense_sa_email}",
  ])
}

data "google_compute_backend_service" "sovereign_api" {
  project = var.project_id
  name    = "god-view-bs-sovereign-api"
}

data "google_compute_backend_service" "cockpit" {
  project = var.project_id
  name    = "god-view-bs-cockpit"
}

resource "google_pubsub_topic" "nervous_system_dlq" {
  project                    = var.project_id
  name                       = "godview-nervous-system-dlq"
  message_retention_duration = "604800s"
  labels                     = local.godview_labels
}

resource "google_pubsub_subscription" "nervous_system_dlq" {
  project                    = var.project_id
  name                       = "godview-nervous-system-dlq-sub"
  topic                      = google_pubsub_topic.nervous_system_dlq.id
  ack_deadline_seconds       = 20
  message_retention_duration = "604800s"
  retain_acked_messages      = true
  labels                     = local.godview_labels
}

resource "google_pubsub_subscription_iam_member" "dlq_sense_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.nervous_system_dlq.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${var.sense_sa_email}"
}

resource "google_pubsub_topic" "nervous_system" {
  project                    = var.project_id
  name                       = "godview-nervous-system"
  message_retention_duration = "86400s"
  labels                     = local.godview_labels
}

resource "google_pubsub_topic_iam_member" "sense_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.nervous_system.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${var.sense_sa_email}"
}

resource "google_pubsub_topic_iam_member" "sense_subscriber" {
  project = var.project_id
  topic   = google_pubsub_topic.nervous_system.name
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${var.sense_sa_email}"
}

resource "google_logging_project_sink" "audit_to_nervous_system" {
  project                = var.project_id
  name                   = "godview-audit-to-nervous-system"
  destination            = "pubsub.googleapis.com/projects/${var.project_id}/topics/${google_pubsub_topic.nervous_system.name}"
  filter                 = "logName:\"logs/cloudaudit.googleapis.com%2Factivity\" AND protoPayload.serviceName:(\"run.googleapis.com\" OR \"container.googleapis.com\" OR \"k8s.io\" OR \"compute.googleapis.com\" OR \"cloudsql.googleapis.com\" OR \"iam.googleapis.com\" OR \"apigateway.googleapis.com\" OR \"secretmanager.googleapis.com\" OR \"cloudresourcemanager.googleapis.com\")"
  unique_writer_identity = true

  exclusions {
    name        = "exclude-gke-leases"
    description = "Exclude GKE lease coordination churn"
    filter      = "protoPayload.serviceName:(\"k8s.io\" OR \"container.googleapis.com\") AND protoPayload.methodName:\"io.k8s.coordination.v1.leases\""
  }

  exclusions {
    name        = "exclude-gke-configmaps"
    description = "Exclude GKE configmaps churn"
    filter      = "protoPayload.serviceName:(\"k8s.io\" OR \"container.googleapis.com\") AND protoPayload.methodName:\"io.k8s.core.v1.configmaps\""
  }

  exclusions {
    name        = "exclude-gke-pods-binding"
    description = "Exclude GKE pods binding churn"
    filter      = "protoPayload.serviceName:(\"k8s.io\" OR \"container.googleapis.com\") AND protoPayload.methodName:\"pods.binding\""
  }

  exclusions {
    name        = "exclude-gke-endpointslices"
    description = "Exclude GKE endpointslices churn"
    filter      = "protoPayload.serviceName:(\"k8s.io\" OR \"container.googleapis.com\") AND protoPayload.methodName:\"endpointslices\""
  }
}

resource "google_pubsub_topic_iam_member" "sink_writer" {
  project = var.project_id
  topic   = google_pubsub_topic.nervous_system.name
  role    = "roles/pubsub.publisher"
  member  = google_logging_project_sink.audit_to_nervous_system.writer_identity
}

# --- God-View Sovereign (min=max=1, 2 vCPU / 2 GiB, cpu_idle=false, Direct VPC egress ALL_TRAFFIC) ---

resource "google_cloud_run_v2_service" "sovereign" {
  project             = var.project_id
  name                = "god-view-sovereign"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.godview_labels

  template {
    service_account                  = var.sense_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.sovereign_min_instances
      max_instance_count = var.sovereign_max_instances
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "god-view-sovereign", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        cpu_idle = false
        limits = {
          cpu    = var.sovereign_cpu
          memory = var.sovereign_memory
        }
      }

      env {
        name  = "SENTINEL_INTERVAL_SECONDS"
        value = tostring(var.hibernate ? 60 : 10)
      }
      env {
        name  = "GEMINI_LOCATION"
        value = "global"
      }
      env {
        name  = "EMBEDDING_LOCATION"
        value = var.region
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = var.firestore_db_name
      }
      env {
        name  = "BIGQUERY_LEDGER_DATASET"
        value = var.ledger_dataset_id
      }
      env {
        name  = "NERVOUS_SYSTEM_TOPIC"
        value = google_pubsub_topic.nervous_system.name
      }
      env {
        name  = "AGENT_SKILLS_BUCKET"
        value = var.agent_skills_bucket
      }
      env {
        name  = "AGENT_STAGING_BUCKET"
        value = var.agent_staging_bucket
      }
      env {
        name  = "TF_STATE_BUCKET"
        value = var.tfstate_bucket
      }
      env {
        name  = "GODVIEW_ACT_SA_EMAIL"
        value = var.act_sa_email
      }
      env {
        name  = "GODVIEW_COURT_MAX_AGENTS"
        value = tostring(var.godview_court_max_agents)
      }
      env {
        name  = "GODVIEW_WARM_AGENTS_MAX"
        value = "2"
      }
      env {
        name = "PG_READONLY_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.pg_readonly_secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "PG_READONLY_SECRET_VERSION"
        value = var.pg_readonly_secret_version_id
      }
      env {
        name  = "GODVIEW_HINT_EXCLUDE"
        value = var.godview_hint_exclude
      }
      env {
        name  = "GODVIEW_CHAT_ENABLED"
        value = "false"
      }
      env {
        name  = "GODVIEW_ENV"
        value = "gcp"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT_NUMBER"
        value = var.project_number
      }
      env {
        name  = "GODVIEW_REGIONS"
        value = "us-central1"
      }
      env {
        name  = "IAP_BACKEND_SERVICE_IDS"
        value = "${data.google_compute_backend_service.sovereign_api.generated_id},${data.google_compute_backend_service.cockpit.generated_id}"
      }
      env {
        name  = "PERCEPTION_INTERVAL_SECONDS"
        value = "300"
      }
      env {
        name  = "GODVIEW_SOVEREIGN_URL"
        value = var.sovereign_service_url != "" ? var.sovereign_service_url : "https://god-view-sovereign-${var.project_number}.${var.region}.run.app"
      }
      env {
        name  = "SERVICE_URL"
        value = var.sovereign_service_url != "" ? var.sovereign_service_url : "https://god-view-sovereign-${var.project_number}.${var.region}.run.app"
      }
      env {
        name = "GODVIEW_SERVICE_PRINCIPALS"
        value = join(",", compact([
          var.sense_sa_email,
          var.act_sa_email,
          var.runtime_sa_email,
          var.deploy_sa_email,
          "service-${var.project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com",
          "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com",
          "service-${var.project_number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com",
          "service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com",
        ]))
      }
      env {
        name  = "GODVIEW_BUDGET_APPLICATION_USD"
        value = tostring(var.token_budget_daily_application_usd)
      }
      env {
        name  = "GODVIEW_BUDGET_BUSINESS_LINE_USD"
        value = tostring(var.token_budget_daily_business_line_usd)
      }
      env {
        name  = "GODVIEW_BUDGET_CHANGE_USD"
        value = tostring(var.token_budget_daily_change_usd)
      }
      env {
        name  = "GODVIEW_BUDGET_DATA_USD"
        value = tostring(var.token_budget_daily_data_usd)
      }
      env {
        name  = "GODVIEW_BUDGET_PLATFORM_USD"
        value = tostring(var.token_budget_daily_platform_usd)
      }
      env {
        name  = "GODVIEW_BUDGET_SOVEREIGN_USD"
        value = tostring(var.token_budget_daily_sovereign_usd)
      }
      env {
        name  = "GODVIEW_COURT_DAILY_CEILING_USD"
        value = tostring(var.token_budget_daily_fleet_ceiling_usd)
      }
      env {
        name  = "GODVIEW_ESCALATION_RESERVE_FRACTION"
        value = "0.20"
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_APPLICATION_USD"
        value = tostring(var.token_budget_daily_application_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_BUSINESS_LINE_USD"
        value = tostring(var.token_budget_daily_business_line_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_CHANGE_USD"
        value = tostring(var.token_budget_daily_change_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_DATA_USD"
        value = tostring(var.token_budget_daily_data_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_PLATFORM_USD"
        value = tostring(var.token_budget_daily_platform_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_SOVEREIGN_USD"
        value = tostring(var.token_budget_daily_sovereign_usd)
      }
      env {
        name  = "TOKEN_BUDGET_DAILY_FLEET_CEILING_USD"
        value = tostring(var.token_budget_daily_fleet_ceiling_usd)
      }
      env {
        name  = "TOKEN_BUDGET_ARGON_RESERVE_PCT"
        value = tostring(var.token_budget_argon_reserve_pct)
      }
      env {
        name  = "DB_HOST"
        value = var.sql_private_ip
      }
      env {
        name  = "DB_USER"
        value = "godview_readonly"
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.pg_readonly_secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GITHUB_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = "godview-github-webhook-secret"
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# --- God-View Cockpit (URL 3: god-view-sre-web, 1 vCPU / 512 MiB, min 0, max 2) ---

resource "google_cloud_run_v2_service" "cockpit" {
  project             = var.project_id
  name                = "god-view-sre-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false
  labels              = local.godview_labels

  template {
    service_account                  = var.sense_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cockpit_max_instances
    }

    containers {
      image = lookup(var.images, "god-view-sre-web", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

resource "google_cloud_run_v2_service_iam_member" "sovereign_invokers" {
  for_each = local.sovereign_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sovereign.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "cockpit_invokers" {
  for_each = local.cockpit_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.cockpit.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_pubsub_subscription" "sovereign_push" {
  project                    = var.project_id
  name                       = "godview-sovereign-push"
  topic                      = google_pubsub_topic.nervous_system.id
  ack_deadline_seconds       = 20
  message_retention_duration = "86400s"
  labels                     = local.godview_labels

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.sovereign.uri}/v1/nervous-system/push"
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = google_cloud_run_v2_service.sovereign.uri
    }
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.nervous_system_dlq.id
    max_delivery_attempts = 5
  }
}

# --- Stage 3: 5 Cloud Scheduler Jobs (Asia/Kolkata, OIDC via godview-sense-sa) ---

resource "google_cloud_scheduler_job" "tenant_pause" {
  project     = var.project_id
  region      = var.region
  name        = "godview-tenant-pause"
  description = "Pause Cymbal tenant traffic at 17:00 IST daily"
  schedule    = "0 17 * * *"
  time_zone   = "Asia/Kolkata"
  paused      = var.hibernate

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.sovereign.uri}/v1/tenant/pause"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      source = "cloud_scheduler"
      action = "pause"
    }))
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = google_cloud_run_v2_service.sovereign.uri
    }
  }
}

resource "google_cloud_scheduler_job" "tenant_resume" {
  project     = var.project_id
  region      = var.region
  name        = "godview-tenant-resume"
  description = "Resume Cymbal tenant traffic at 09:00 IST daily"
  schedule    = "0 9 * * *"
  time_zone   = "Asia/Kolkata"
  paused      = var.hibernate

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.sovereign.uri}/v1/tenant/resume"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      source = "cloud_scheduler"
      action = "resume"
    }))
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = google_cloud_run_v2_service.sovereign.uri
    }
  }
}

resource "google_cloud_scheduler_job" "probe_run" {
  project     = var.project_id
  region      = var.region
  name        = "godview-probe-run"
  description = "Run synthetic verification probes every 5 min during active window (09:00-17:00 IST)"
  schedule    = "*/5 9-16 * * *"
  time_zone   = "Asia/Kolkata"
  paused      = var.hibernate

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.sovereign.uri}/v1/probes/run"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      source = "cloud_scheduler"
      action = "probe_run"
    }))
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = google_cloud_run_v2_service.sovereign.uri
    }
  }
}

resource "google_cloud_scheduler_job" "agent_reflection" {
  project     = var.project_id
  region      = var.region
  name        = "godview-agent-reflection"
  description = "Nightly episodic-to-procedural memory consolidation at 03:30 IST"
  schedule    = "30 3 * * *"
  time_zone   = "Asia/Kolkata"
  paused      = var.hibernate

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.sovereign.uri}/v1/memory/reflect"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      source = "cloud_scheduler"
      action = "reflect"
    }))
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = google_cloud_run_v2_service.sovereign.uri
    }
  }
}

resource "google_cloud_scheduler_job" "simulator_tick" {
  project     = var.project_id
  region      = var.region
  name        = "godview-simulator-tick"
  description = "Minute tick for ai-customer-simulator during 09:00-17:00 IST (300/480 probability per tick)"
  schedule    = "* 9-16 * * *"
  time_zone   = "Asia/Kolkata"
  paused      = var.hibernate

  http_target {
    http_method = "POST"
    uri         = "${var.simulator_service_url}/v1/simulator/tick"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      source = "cloud_scheduler"
      action = "tick"
    }))
    oidc_token {
      service_account_email = var.sense_sa_email
      audience              = var.simulator_service_url
    }
  }
}

# --- Stage 3: Cloud Monitoring Uptime Check, Heartbeat Alert & Notification Channel ---

resource "google_monitoring_notification_channel" "operator_email" {
  project      = var.project_id
  display_name = "God-View Operator Email"
  type         = "email"
  user_labels  = local.godview_labels

  labels = {
    email_address = var.operator_email
  }
}

resource "google_monitoring_uptime_check_config" "sovereign_health" {
  project      = var.project_id
  display_name = "god-view-sovereign-uptime"
  timeout      = "10s"
  period       = "60s"
  user_labels  = local.godview_labels

  http_check {
    path    = "/public/healthz"
    port    = 443
    use_ssl = true

    service_agent_authentication {
      type = "OIDC_TOKEN"
    }
  }

  monitored_resource {
    type = "cloud_run_revision"
    labels = {
      project_id   = var.project_id
      location     = var.region
      service_name = google_cloud_run_v2_service.sovereign.name
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_monitoring_alert_policy" "sovereign_heartbeat" {
  project               = var.project_id
  display_name          = "God-View Sovereign Heartbeat & Uptime Alert"
  combiner              = "OR"
  user_labels           = local.godview_labels
  notification_channels = [google_monitoring_notification_channel.operator_email.name]

  conditions {
    display_name = "Sovereign Uptime Check Failure"

    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"cloud_run_revision\" AND metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.sovereign_health.uptime_check_id}\""
      duration        = "180s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1

      trigger {
        count = 1
      }

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }
}

# --- Stage 3: Billing Budget ($600/mo, 50/80/100%, count = var.create_budget ? 1 : 0) ---

resource "google_billing_budget" "godview" {
  count           = var.create_budget && var.billing_account_id != "" ? 1 : 0
  billing_account = var.billing_account_id
  display_name    = "god-view-monthly-budget-600"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
    labels = {
      godview_managed = "true"
    }
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "600"
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.operator_email.id
    ]
    disable_default_iam_recipients = false
  }
}

# --- Stage 3: Cloud Run Job god-view-hibernate-job ---

resource "google_cloud_run_v2_job" "hibernate" {
  project             = var.project_id
  name                = "god-view-hibernate-job"
  location            = var.region
  deletion_protection = false
  labels              = local.godview_labels

  template {
    template {
      service_account = var.act_sa_email
      max_retries     = 1
      timeout         = "600s"

      containers {
        image = lookup(var.images, "god-view-hibernate-job", "us-docker.pkg.dev/cloudrun/container/hello")

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GCP_REGION"
          value = var.region
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

# --- Stage 3: Vertex AI Reasoning Engine Service Agent IAM on Staging Bucket ---

resource "google_storage_bucket_iam_member" "staging_re_service_agent" {
  bucket = var.agent_staging_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}
