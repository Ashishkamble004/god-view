output "network_id" {
  value = google_compute_network.vpc.id
}

output "network_name" {
  value = google_compute_network.vpc.name
}

output "subnet_id" {
  value = google_compute_subnetwork.primary.id
}

output "subnet_name" {
  value = google_compute_subnetwork.primary.name
}

output "subnet_cidr" {
  value = google_compute_subnetwork.primary.ip_cidr_range
}

output "psa_range_cidr" {
  value = "${google_compute_global_address.psa_range.address}/${google_compute_global_address.psa_range.prefix_length}"
}

output "psa_connection_id" {
  value = google_service_networking_connection.psa.id
}

output "kyc_ilb_ip" {
  value = google_compute_address.kyc_ilb.address
}

output "kyc_ilb_address_name" {
  value = google_compute_address.kyc_ilb.name
}

output "los_ilb_ip" {
  value = google_compute_address.los_ilb.address
}

output "los_ilb_address_name" {
  value = google_compute_address.los_ilb.name
}

output "lb_ip_address" {
  value = google_compute_global_address.lb_ip.address
}

output "cloud_armor_policy_name" {
  value = google_compute_security_policy.edge_policy.name
}

output "control_plane_armor_policy_name" {
  value = google_compute_security_policy.control_policy.name
}
