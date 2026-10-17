output "nervous_system_topic" {
  value = google_pubsub_topic.nervous_system.name
}

output "nervous_system_dlq_topic" {
  value = google_pubsub_topic.nervous_system_dlq.name
}

output "nervous_system_dlq_subscription" {
  value = google_pubsub_subscription.nervous_system_dlq.name
}

output "sovereign_service_name" {
  value = google_cloud_run_v2_service.sovereign.name
}

output "sovereign_service_url" {
  value = google_cloud_run_v2_service.sovereign.uri
}

output "sre_web_service_name" {
  value = google_cloud_run_v2_service.cockpit.name
}

output "sre_web_service_url" {
  value = google_cloud_run_v2_service.cockpit.uri
}

output "hibernate_job_name" {
  value = google_cloud_run_v2_job.hibernate.name
}
