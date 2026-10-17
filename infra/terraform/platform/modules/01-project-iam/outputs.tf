output "sense_sa_email" {
  value = google_service_account.sense.email
}

output "sense_sa_name" {
  value = google_service_account.sense.name
}

output "act_sa_email" {
  value = google_service_account.act.email
}

output "act_sa_name" {
  value = google_service_account.act.name
}

output "runtime_sa_email" {
  value = google_service_account.runtime.email
}

output "runtime_sa_name" {
  value = google_service_account.runtime.name
}

output "apigw_sa_email" {
  value = google_service_account.apigw.email
}

output "apigw_sa_name" {
  value = google_service_account.apigw.name
}

output "deploy_sa_email" {
  value = google_service_account.deploy.email
}

output "wif_provider_name" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "artifact_registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
}

output "apis_enabled" {
  value = [for k, v in google_project_service.apis : v.service]
}
