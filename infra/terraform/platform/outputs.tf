output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "sense_sa_email" {
  value = module.project_iam.sense_sa_email
}

output "act_sa_email" {
  value = module.project_iam.act_sa_email
}

output "runtime_sa_email" {
  value = module.project_iam.runtime_sa_email
}

output "apigw_sa_email" {
  value = module.project_iam.apigw_sa_email
}

output "deploy_sa_email" {
  value = module.project_iam.deploy_sa_email
}

output "wif_provider_name" {
  value = module.project_iam.wif_provider_name
}

output "artifact_registry_url" {
  value = module.project_iam.artifact_registry_url
}

output "network_id" {
  value = module.network_edge.network_id
}

output "subnet_id" {
  value = module.network_edge.subnet_id
}

output "subnet_cidr" {
  value = module.network_edge.subnet_cidr
}

output "psa_range_cidr" {
  value = module.network_edge.psa_range_cidr
}

output "psa_cidr" {
  value = module.network_edge.psa_range_cidr
}

output "psa_range" {
  value = module.network_edge.psa_range_cidr
}

output "kyc_ilb_address" {
  value = module.network_edge.kyc_ilb_ip
}

output "kyc_ilb_address_name" {
  value = module.network_edge.kyc_ilb_address_name
}

output "los_ilb_address" {
  value = module.network_edge.los_ilb_ip
}

output "los_ilb_address_name" {
  value = module.network_edge.los_ilb_address_name
}

output "ilb_addresses" {
  value = {
    kyc              = module.network_edge.kyc_ilb_ip
    kyc_name         = module.network_edge.kyc_ilb_address_name
    kyc_ip           = module.network_edge.kyc_ilb_ip
    kyc_address_name = module.network_edge.kyc_ilb_address_name
    los              = module.network_edge.los_ilb_ip
    los_name         = module.network_edge.los_ilb_address_name
    los_ip           = module.network_edge.los_ilb_ip
    los_address_name = module.network_edge.los_ilb_address_name
  }
}

output "lb_ip_address" {
  value = module.network_edge.lb_ip_address
}

output "cloud_armor_policy_name" {
  value = module.network_edge.cloud_armor_policy_name
}

output "sql_private_ip" {
  value = module.data.sql_private_ip
}

output "sql_connection_name" {
  value = module.data.sql_connection_name
}

output "firestore_db_name" {
  value = module.data.firestore_db_name
}

output "ledger_dataset_id" {
  value = module.data.ledger_dataset_id
}

output "bucket_names" {
  value = module.data.bucket_names
}

output "secret_ids" {
  value = module.data.secret_ids
}

output "gke_cluster_name" {
  value = module.tenant_compute.gke_cluster_name
}

output "gke_endpoint" {
  value = module.tenant_compute.gke_endpoint
}

output "gke_dns_endpoint" {
  value = module.tenant_compute.gke_dns_endpoint
}

output "cbs_vm_internal_ip" {
  value = module.tenant_compute.cbs_vm_internal_ip
}

output "model_armor_template_id" {
  value = module.tenant_compute.model_armor_template_id
}

output "api_gateway_hostname" {
  value = module.tenant_compute.api_gateway_hostname
}

output "tenant_service_urls" {
  value = module.tenant_compute.service_urls
}

output "sovereign_service_url" {
  value = module.godview.sovereign_service_url
}

output "sre_web_service_url" {
  value = module.godview.sre_web_service_url
}

output "nervous_system_topic" {
  value = module.godview.nervous_system_topic
}

output "nervous_system_dlq_topic" {
  value = module.godview.nervous_system_dlq_topic
}

output "nervous_system_dlq_subscription" {
  value = module.godview.nervous_system_dlq_subscription
}

output "hibernate_job_name" {
  value = module.godview.hibernate_job_name
}
