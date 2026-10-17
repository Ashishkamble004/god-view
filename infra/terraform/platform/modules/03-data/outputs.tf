output "sql_private_ip" {
  value = google_sql_database_instance.god_view_pg.private_ip_address
}

output "sql_connection_name" {
  value = google_sql_database_instance.god_view_pg.connection_name
}

output "sql_instance_name" {
  value = google_sql_database_instance.god_view_pg.name
}

output "sql_database_name" {
  value = google_sql_database.godview_lms.name
}

output "firestore_db_name" {
  value = google_firestore_database.memory.name
}

output "ledger_dataset_id" {
  value = google_bigquery_dataset.godview_ledger.dataset_id
}

output "bucket_names" {
  value = {
    kyc_vault     = google_storage_bucket.kyc_vault.name
    cdn_assets    = google_storage_bucket.cdn_assets.name
    agent_skills  = google_storage_bucket.agent_skills.name
    tfstate       = google_storage_bucket.tfstate.name
    agent_staging = google_storage_bucket.agent_staging.name
  }
}

output "secret_ids" {
  value = {
    cymbal_pg_app_password        = google_secret_manager_secret.cymbal_pg_app_password.secret_id
    godview_github_app_key        = google_secret_manager_secret.godview_github_app_key.secret_id
    godview_github_webhook_secret = google_secret_manager_secret.godview_github_webhook_secret.secret_id
    godview_pg_readonly_password  = google_secret_manager_secret.godview_pg_readonly_password.secret_id
  }
}

output "pg_readonly_secret_version_id" {
  value = google_secret_manager_secret_version.godview_pg_readonly_password.id
  depends_on = [
    google_secret_manager_secret_iam_member.pg_readonly_pass_sense
  ]
}
