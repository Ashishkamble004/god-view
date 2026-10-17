provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_client_config" "default" {
  count = var.provider_mode == "gcp" ? 1 : 0
}

data "google_container_cluster" "autopilot" {
  count    = var.provider_mode == "gcp" ? 1 : 0
  name     = local.gke_cluster_name
  location = var.region
  project  = var.project_id
}

provider "kubernetes" {
  host = var.provider_mode == "gcp" ? (
    local.gke_dns_endpoint != ""
    ? "https://${local.gke_dns_endpoint}"
    : "https://${data.google_container_cluster.autopilot[0].endpoint}"
  ) : null

  token = var.provider_mode == "gcp" ? data.google_client_config.default[0].access_token : null

  cluster_ca_certificate = (
    var.provider_mode == "gcp" && local.gke_dns_endpoint == ""
    ? base64decode(data.google_container_cluster.autopilot[0].master_auth[0].cluster_ca_certificate)
    : null
  )

  config_path    = var.provider_mode == "kind" ? var.kubeconfig_path : null
  config_context = var.provider_mode == "kind" ? var.kube_context : null
}
