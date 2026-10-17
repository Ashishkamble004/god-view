locals {
  cymbal_labels = {
    godview_managed = "true"
    tf_module       = "03-data"
    tenant          = "cymbal"
  }

  godview_labels = {
    godview_managed = "true"
    tf_module       = "03-data"
    tenant          = "godview"
    godview_role    = "observer"
  }

  repo_schema_dir  = "${path.module}/../../../../../godview/ledger/schemas"
  local_schema_dir = "${path.module}/schemas"
}

# --- GCS Buckets (5) ---

resource "google_storage_bucket" "kyc_vault" {
  project                     = var.project_id
  name                        = "${var.project_id}-god-view-kyc-vault"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.cymbal_labels
}

resource "google_storage_bucket" "cdn_assets" {
  project                     = var.project_id
  name                        = "${var.project_id}-god-view-cdn-assets"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "inherited"
  force_destroy               = true
  labels                      = local.cymbal_labels
}

resource "google_storage_bucket" "agent_skills" {
  project                     = var.project_id
  name                        = "${var.project_id}-god-view-agent-skills"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.godview_labels
}

resource "google_storage_bucket" "tfstate" {
  project                     = var.project_id
  name                        = "${var.project_id}-god-view-tfstate"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.godview_labels

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket" "agent_staging" {
  project                     = var.project_id
  name                        = "${var.project_id}-god-view-agent-staging"
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.godview_labels
}

resource "google_storage_bucket_iam_member" "tfstate_sense_viewer" {
  bucket = google_storage_bucket.tfstate.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.sense_sa_email}"
}

resource "google_storage_bucket_iam_member" "skills_sense_admin" {
  bucket = google_storage_bucket.agent_skills.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.sense_sa_email}"
}

resource "google_storage_bucket_iam_member" "staging_sense_admin" {
  bucket = google_storage_bucket.agent_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.sense_sa_email}"
}

resource "google_storage_bucket_iam_member" "kyc_runtime_user" {
  bucket = google_storage_bucket.kyc_vault.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.runtime_sa_email}"
}

resource "google_storage_bucket_iam_member" "cdn_runtime_user" {
  bucket = google_storage_bucket.cdn_assets.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.runtime_sa_email}"
}

# --- Secret Manager (3 secrets) ---

resource "random_password" "pg_app" {
  length  = 24
  special = false
}

resource "random_password" "pg_readonly" {
  length  = 24
  special = false
  keepers = {
    rotation = "v5-secret-manager"
  }
}

resource "random_password" "webhook_secret" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "godview_pg_readonly_password" {
  project   = var.project_id
  secret_id = "godview-pg-readonly-password"
  labels    = local.godview_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "godview_pg_readonly_password" {
  secret      = google_secret_manager_secret.godview_pg_readonly_password.id
  secret_data = random_password.pg_readonly.result
}

resource "google_secret_manager_secret_iam_member" "pg_readonly_pass_sense" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.godview_pg_readonly_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.sense_sa_email}"
}

resource "google_secret_manager_secret" "cymbal_pg_app_password" {
  project   = var.project_id
  secret_id = "cymbal-pg-app-password"
  labels    = local.cymbal_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "cymbal_pg_app_password" {
  secret      = google_secret_manager_secret.cymbal_pg_app_password.id
  secret_data = random_password.pg_app.result
}

resource "google_secret_manager_secret_iam_member" "pg_pass_runtime" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.cymbal_pg_app_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.runtime_sa_email}"
}

resource "google_secret_manager_secret" "godview_github_app_key" {
  project   = var.project_id
  secret_id = "godview-github-app-key"
  labels    = local.godview_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "github_key_sense" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.godview_github_app_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.sense_sa_email}"
}

resource "google_secret_manager_secret" "godview_github_webhook_secret" {
  project   = var.project_id
  secret_id = "godview-github-webhook-secret"
  labels    = local.godview_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "godview_github_webhook_secret" {
  secret      = google_secret_manager_secret.godview_github_webhook_secret.id
  secret_data = random_password.webhook_secret.result
}

resource "google_secret_manager_secret_iam_member" "github_webhook_sense" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.godview_github_webhook_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.sense_sa_email}"
}

# --- Firestore Native DB + 4 Vector Indexes (A17) ---

resource "google_firestore_database" "memory" {
  project                 = var.project_id
  name                    = "god-view-memory-db"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
}

resource "google_firestore_index" "service_purpose" {
  project     = var.project_id
  database    = google_firestore_database.memory.name
  collection  = "discovered_services"
  query_scope = "COLLECTION"

  fields {
    field_path = "purpose_embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }

  lifecycle {
    ignore_changes = [fields]
  }
}

resource "google_firestore_index" "invariant_statement" {
  project     = var.project_id
  database    = google_firestore_database.memory.name
  collection  = "invariants"
  query_scope = "COLLECTION"

  fields {
    field_path = "statement_embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }

  lifecycle {
    ignore_changes = [fields]
  }
}

resource "google_firestore_index" "episodic_symptom" {
  project     = var.project_id
  database    = google_firestore_database.memory.name
  collection  = "episodic_incidents"
  query_scope = "COLLECTION"

  fields {
    field_path = "symptom_embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }

  lifecycle {
    ignore_changes = [fields]
  }
}

resource "google_firestore_index" "skill_trigger" {
  project     = var.project_id
  database    = google_firestore_database.memory.name
  collection  = "procedural_skills"
  query_scope = "COLLECTION"

  fields {
    field_path = "trigger_embedding"
    vector_config {
      dimension = 768
      flat {}
    }
  }

  lifecycle {
    ignore_changes = [fields]
  }
}

# --- BigQuery Business Event Ledger (dataset + 4 tables) ---

resource "google_bigquery_dataset" "godview_ledger" {
  project                    = var.project_id
  dataset_id                 = "godview_ledger"
  friendly_name              = "God-View Business Event Ledger"
  description                = "Business event, state-transition, journey and KPI rollup ledger"
  location                   = var.region
  delete_contents_on_destroy = true
  labels                     = local.godview_labels
}

resource "google_bigquery_dataset_iam_member" "ledger_sense_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.godview_ledger.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.sense_sa_email}"
}

resource "google_bigquery_dataset_iam_member" "ledger_runtime_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.godview_ledger.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.runtime_sa_email}"
}

resource "google_bigquery_table" "events" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.godview_ledger.dataset_id
  table_id            = "events"
  deletion_protection = false
  labels              = local.godview_labels
  # Resolves godview/ledger/schemas/events.json once delivered by ledger-invariants, with module-local fallback
  schema     = fileexists("${local.repo_schema_dir}/events.json") ? file("${local.repo_schema_dir}/events.json") : file("${local.local_schema_dir}/events.json")
  clustering = ["journey_id", "service", "event_type"]

  time_partitioning {
    type  = "DAY"
    field = "event_time"
  }
}

resource "google_bigquery_table" "state_transitions" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.godview_ledger.dataset_id
  table_id            = "state_transitions"
  deletion_protection = false
  labels              = local.godview_labels
  schema              = fileexists("${local.repo_schema_dir}/state_transitions.json") ? file("${local.repo_schema_dir}/state_transitions.json") : file("${local.local_schema_dir}/state_transitions.json")
  clustering          = ["entity", "journey_id", "status_to"]

  time_partitioning {
    type  = "DAY"
    field = "transition_time"
  }
}

resource "google_bigquery_table" "journeys" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.godview_ledger.dataset_id
  table_id            = "journeys"
  deletion_protection = false
  labels              = local.godview_labels
  schema              = fileexists("${local.repo_schema_dir}/journeys.json") ? file("${local.repo_schema_dir}/journeys.json") : file("${local.local_schema_dir}/journeys.json")
  clustering          = ["journey_name", "conformance", "final_status"]

  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
}

resource "google_bigquery_table" "kpi_rollups" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.godview_ledger.dataset_id
  table_id            = "kpi_rollups"
  deletion_protection = false
  labels              = local.godview_labels
  schema              = fileexists("${local.repo_schema_dir}/kpi_rollups.json") ? file("${local.repo_schema_dir}/kpi_rollups.json") : file("${local.local_schema_dir}/kpi_rollups.json")
  clustering          = ["journey_name", "kpi"]

  time_partitioning {
    type  = "DAY"
    field = "window_start"
  }
}

# --- Cloud SQL PostgreSQL 16 (god-view-pg) ---

resource "google_sql_database_instance" "god_view_pg" {
  project             = var.project_id
  name                = "god-view-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = false

  settings {
    tier              = var.sql_tier
    edition           = "ENTERPRISE"
    availability_type = var.sql_availability_type
    disk_size         = var.sql_disk_gb
    disk_type         = "PD_SSD"
    activation_policy = var.hibernate ? "NEVER" : "ALWAYS"
    user_labels       = local.cymbal_labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false
    }

    database_flags {
      name  = "cloudsql.logical_decoding"
      value = "on"
    }

    database_flags {
      name  = "pg_stat_statements.track"
      value = "all"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  depends_on = [var.psa_connection_id]
}

resource "google_sql_database" "godview_lms" {
  project  = var.project_id
  name     = "godview_lms"
  instance = google_sql_database_instance.god_view_pg.name
}

resource "google_sql_user" "app_user" {
  project  = var.project_id
  name     = "app_user"
  instance = google_sql_database_instance.god_view_pg.name
  password = random_password.pg_app.result
}

resource "google_sql_user" "godview_readonly" {
  project  = var.project_id
  name     = "godview_readonly"
  instance = google_sql_database_instance.god_view_pg.name
  password = random_password.pg_readonly.result

  depends_on = [
    google_secret_manager_secret_version.godview_pg_readonly_password,
    google_secret_manager_secret_iam_member.pg_readonly_pass_sense,
  ]
}

# --- Datastream CDC (Phase 2, gated by var.enable_datastream) ---

resource "google_datastream_connection_profile" "bigquery_dest" {
  count                 = var.enable_datastream ? 1 : 0
  project               = var.project_id
  location              = var.region
  connection_profile_id = "god-view-bq-dest"
  display_name          = "God-View Ledger BigQuery Destination (Phase 2)"
  labels                = local.godview_labels

  bigquery_profile {}
}
