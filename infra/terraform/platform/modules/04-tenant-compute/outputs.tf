output "gke_cluster_name" {
  value = google_container_cluster.autopilot.name
}

output "gke_endpoint" {
  value = google_container_cluster.autopilot.endpoint
}

output "gke_dns_endpoint" {
  value = try(google_container_cluster.autopilot.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint, google_container_cluster.autopilot.endpoint)
}

output "cbs_vm_internal_ip" {
  value = google_compute_instance.cbs_vm.network_interface[0].network_ip
}

output "model_armor_template_id" {
  value = google_model_armor_template.text_guard.name
}

output "cloud_run_uris" {
  value = {
    loan_orchestrator_api       = google_cloud_run_v2_service.loan_orchestrator.uri
    commercial_fleet_credit_svc = google_cloud_run_v2_service.commercial_fleet_credit.uri
    motor_insurance_svc         = google_cloud_run_v2_service.motor_insurance.uri
    customer_loan_portal_web    = google_cloud_run_v2_service.customer_portal_web.uri
    internal_bank_los_web       = google_cloud_run_v2_service.internal_los_web.uri
    ai_customer_simulator       = google_cloud_run_v2_service.ai_customer_simulator.uri
  }
}

output "simulator_uri" {
  value = google_cloud_run_v2_service.ai_customer_simulator.uri
}

output "simulator_service_url" {
  value = google_cloud_run_v2_service.ai_customer_simulator.uri
}

output "service_urls" {
  value = {
    loan_orchestrator_api       = google_cloud_run_v2_service.loan_orchestrator.uri
    commercial_fleet_credit_svc = google_cloud_run_v2_service.commercial_fleet_credit.uri
    motor_insurance_svc         = google_cloud_run_v2_service.motor_insurance.uri
    customer_loan_portal_web    = google_cloud_run_v2_service.customer_portal_web.uri
    internal_bank_los_web       = google_cloud_run_v2_service.internal_los_web.uri
    ai_customer_simulator       = google_cloud_run_v2_service.ai_customer_simulator.uri
  }
}

output "portal_service_name" {
  value = google_cloud_run_v2_service.customer_portal_web.name
}

output "portal_web_service_name" {
  value = google_cloud_run_v2_service.customer_portal_web.name
}

output "los_service_name" {
  value = google_cloud_run_v2_service.internal_los_web.name
}

output "los_web_service_name" {
  value = google_cloud_run_v2_service.internal_los_web.name
}

output "api_gateway_id" {
  value = google_api_gateway_gateway.loan_api_gw.gateway_id
}

output "api_gateway_default_hostname" {
  value = google_api_gateway_gateway.loan_api_gw.default_hostname
}

output "api_gateway_hostname" {
  value = google_api_gateway_gateway.loan_api_gw.default_hostname
}
