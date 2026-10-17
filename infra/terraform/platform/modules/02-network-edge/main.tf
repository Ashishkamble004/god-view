terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
    google-beta = {
      source = "hashicorp/google-beta"
    }
  }
}

locals {
  cymbal_labels = {
    godview_managed = "true"
    tf_module       = "02-network-edge"
    tenant          = "cymbal"
  }

  dns_records = {
    cymbal  = "cymbal.ak-demos.com."
    los     = "los.cymbal.ak-demos.com."
    api     = "api.cymbal.ak-demos.com."
    godview = "godview.ak-demos.com."
  }
}

# --- VPC, Subnet, PSA, Cloud NAT, ILB IPs, Firewalls ---

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "god-view-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "primary" {
  project                  = var.project_id
  name                     = "god-view-subnet"
  ip_cidr_range            = "10.10.0.0/20"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

resource "google_compute_global_address" "psa_range" {
  project       = var.project_id
  name          = "god-view-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
  labels        = local.cymbal_labels
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range.name]
}

resource "google_compute_router" "nat" {
  project = var.project_id
  name    = "god-view-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "god-view-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_address" "kyc_ilb" {
  project      = var.project_id
  name         = "god-view-kyc-ilb-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.primary.id
  address_type = "INTERNAL"
  purpose      = "GCE_ENDPOINT"
  labels       = local.cymbal_labels
}

resource "google_compute_address" "los_ilb" {
  project      = var.project_id
  name         = "god-view-los-ilb-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.primary.id
  address_type = "INTERNAL"
  purpose      = "GCE_ENDPOINT"
  labels       = local.cymbal_labels
}

resource "google_compute_firewall" "allow_subnet_to_gke_ilb" {
  project       = var.project_id
  name          = "gv-allow-subnet-to-gke-ilb"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = [google_compute_subnetwork.primary.ip_cidr_range, "10.163.128.0/17", "10.0.0.0/8"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_compute_firewall" "allow_subnet_to_cbs" {
  project                 = var.project_id
  name                    = "gv-allow-subnet-to-cbs"
  network                 = google_compute_network.vpc.name
  direction               = "INGRESS"
  source_ranges           = [google_compute_subnetwork.primary.ip_cidr_range, "10.163.128.0/17", "10.0.0.0/8"]
  target_service_accounts = [var.runtime_sa_email]

  allow {
    protocol = "tcp"
    ports    = ["8085"]
  }
}

resource "google_compute_firewall" "allow_egress_cloudsql" {
  project            = var.project_id
  name               = "gv-allow-egress-cloudsql"
  network            = google_compute_network.vpc.name
  direction          = "EGRESS"
  destination_ranges = ["${google_compute_global_address.psa_range.address}/${google_compute_global_address.psa_range.prefix_length}"]

  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
}

resource "google_compute_firewall" "allow_health_checks" {
  project       = var.project_id
  name          = "gv-allow-health-checks"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_compute_firewall" "allow_iap_ssh" {
  project       = var.project_id
  name          = "gv-allow-iap-ssh"
  network       = google_compute_network.vpc.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# --- Global Anycast IP + Cloud Armor Edge Security Policy ---

resource "google_compute_global_address" "lb_ip" {
  project = var.project_id
  name    = "god-view-lb-ip"
  labels  = local.cymbal_labels
}

resource "google_compute_security_policy" "edge_policy" {
  project     = var.project_id
  name        = "god-view-edge-policy"
  description = "Cloud Armor WAF, rate limit, and Kill Switch policy for Cymbal and God-View edge"

  rule {
    action      = "deny(403)"
    priority    = 100
    preview     = true
    description = "Kill Switch emergency block rule (toggled by God-View Kill Switch / Hibernate)"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 1000
    description = "OWASP Top-10 preconfigured WAF rules (SQLi, XSS, LFI, RFI)"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable') || evaluatePreconfiguredWaf('xss-v33-stable') || evaluatePreconfiguredWaf('lfi-v33-stable') || evaluatePreconfiguredWaf('rfi-v33-stable')"
      }
    }
  }

  rule {
    action      = "throttle"
    priority    = 2000
    description = "Per-IP rate limiting (120 req/min)"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 120
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default allow rule"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# --- God-View control-plane edge policy (cockpit + Sovereign API/public routes) ---
# The control plane must never fate-share with the tenant edge policy above: that policy
# carries the tenant Kill Switch rule that God-View itself toggles, and its WAF tuning is a
# tenant concern God-View is expected to detect and remediate. A God-View lock-out caused by
# the system it is supposed to repair is a design flaw (see issues-identified doc, 2026-10-17).
# WAF rules are pinned to sensitivity 1 (CRS paranoia level 1). Without an explicit sensitivity
# evaluatePreconfiguredWaf() enables every signature up to paranoia level 4, including
# 942432 "Restricted SQL Character Anomaly Detection (args) >= 2 special characters", which
# rejects IAP's OAuth callback (`state` token) and every JSON request body with HTTP 403.
resource "google_compute_security_policy" "control_policy" {
  project     = var.project_id
  name        = "god-view-control-policy"
  description = "Cloud Armor policy for the God-View control plane (cockpit + Sovereign); independent of the tenant edge policy"

  advanced_options_config {
    json_parsing = "STANDARD"
    log_level    = "VERBOSE"
  }

  rule {
    action      = "deny(403)"
    priority    = 1000
    description = "OWASP Top-10 preconfigured WAF rules (SQLi, XSS, LFI, RFI) at sensitivity 1"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('rfi-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    action      = "throttle"
    priority    = 2000
    description = "Per-IP rate limiting (600 req/min; the cockpit polls ~15 endpoints every 10 s without SSE)"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default allow rule"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# --- 5 Serverless NEGs ---

resource "google_compute_region_network_endpoint_group" "portal" {
  project               = var.project_id
  name                  = "god-view-neg-portal"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.portal_web_service_name
  }
}

resource "google_compute_region_network_endpoint_group" "los" {
  project               = var.project_id
  name                  = "god-view-neg-los"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.los_web_service_name
  }
}

resource "google_compute_region_network_endpoint_group" "apigw" {
  provider              = google-beta
  project               = var.project_id
  name                  = "god-view-neg-apigw"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  serverless_deployment {
    platform = "apigateway.googleapis.com"
    resource = var.api_gateway_id
  }
}

resource "google_compute_region_network_endpoint_group" "sovereign" {
  project               = var.project_id
  name                  = "god-view-neg-sovereign"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.sovereign_service_name
  }
}

resource "google_compute_region_network_endpoint_group" "cockpit" {
  project               = var.project_id
  name                  = "god-view-neg-cockpit"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.sre_web_service_name
  }
}

# --- 6 Backend Services + 1 CDN Backend Bucket ---

resource "google_compute_backend_service" "portal" {
  project               = var.project_id
  name                  = "god-view-bs-portal"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.edge_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.portal.id
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_service" "los" {
  project               = var.project_id
  name                  = "god-view-bs-los"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.edge_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.los.id
  }

  iap {
    enabled = true
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_service" "apigw" {
  project               = var.project_id
  name                  = "god-view-bs-apigw"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.edge_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.apigw.id
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_service" "cockpit" {
  project               = var.project_id
  name                  = "god-view-bs-cockpit"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.control_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.cockpit.id
  }

  iap {
    enabled = true
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_service" "sovereign_api" {
  project               = var.project_id
  name                  = "god-view-bs-sovereign-api"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.control_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.sovereign.id
  }

  iap {
    enabled = true
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_service" "sovereign_public" {
  project               = var.project_id
  name                  = "god-view-bs-sovereign-public"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.control_policy.id

  backend {
    group = google_compute_region_network_endpoint_group.sovereign.id
  }

  # LB request logs make Cloud Armor / IAP decisions observable (statusDetails,
  # enforcedSecurityPolicy.preconfiguredExprIds). Without them the edge is a blind spot.
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_backend_bucket" "cdn" {
  project     = var.project_id
  name        = "god-view-bb-cdn"
  bucket_name = var.cdn_assets_bucket_name
  enable_cdn  = true

  cdn_policy {
    cache_mode  = "CACHE_ALL_STATIC"
    default_ttl = 3600
    max_ttl     = 86400
    client_ttl  = 3600
  }
}

# --- IAP Accessor Bindings (admin@kambleashish.altostrat.com) ---

resource "google_iap_web_backend_service_iam_member" "los_operator" {
  project             = var.project_id
  web_backend_service = google_compute_backend_service.los.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "user:${var.operator_email}"
}

resource "google_iap_web_backend_service_iam_member" "cockpit_operator" {
  project             = var.project_id
  web_backend_service = google_compute_backend_service.cockpit.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "user:${var.operator_email}"
}

resource "google_iap_web_backend_service_iam_member" "sovereign_api_operator" {
  project             = var.project_id
  web_backend_service = google_compute_backend_service.sovereign_api.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = "user:${var.operator_email}"
}

# --- Managed SSL Certificate, URL Maps, Target Proxies, Forwarding Rules ---

resource "google_compute_managed_ssl_certificate" "edge_cert" {
  project = var.project_id
  name    = "god-view-edge-cert"

  managed {
    domains = [
      "cymbal.ak-demos.com",
      "los.cymbal.ak-demos.com",
      "api.cymbal.ak-demos.com",
      "godview.ak-demos.com",
    ]
  }
}

resource "google_compute_url_map" "edge" {
  project         = var.project_id
  name            = "god-view-edge"
  default_service = google_compute_backend_service.portal.id

  host_rule {
    hosts        = ["cymbal.ak-demos.com"]
    path_matcher = "pm-cymbal"
  }

  host_rule {
    hosts        = ["los.cymbal.ak-demos.com"]
    path_matcher = "pm-los"
  }

  host_rule {
    hosts        = ["api.cymbal.ak-demos.com"]
    path_matcher = "pm-api"
  }

  host_rule {
    hosts        = ["godview.ak-demos.com"]
    path_matcher = "pm-godview"
  }

  path_matcher {
    name            = "pm-cymbal"
    default_service = google_compute_backend_service.portal.id

    path_rule {
      paths   = ["/static/*"]
      service = google_compute_backend_bucket.cdn.id
    }

    path_rule {
      paths   = ["/api", "/api/*"]
      service = google_compute_backend_service.apigw.id
    }
  }

  path_matcher {
    name            = "pm-los"
    default_service = google_compute_backend_service.los.id

    path_rule {
      paths   = ["/api", "/api/*"]
      service = google_compute_backend_service.apigw.id
    }
  }

  path_matcher {
    name            = "pm-api"
    default_service = google_compute_backend_service.apigw.id
  }

  path_matcher {
    name            = "pm-godview"
    default_service = google_compute_backend_service.cockpit.id

    path_rule {
      paths   = ["/api/*", "/api/v1/*", "/v1/*"]
      service = google_compute_backend_service.sovereign_api.id
    }

    # Health probes on the God-View host must hit the Sovereign itself. They previously routed
    # to the tenant customer portal, so the `god-view-sovereign-uptime` check reported "healthy"
    # even when the Sovereign was down (false heartbeat).
    path_rule {
      paths   = ["/public/healthz", "/healthz"]
      service = google_compute_backend_service.sovereign_public.id
    }

    path_rule {
      paths   = ["/public/*", "/webhooks/github", "/public/webhooks/github"]
      service = google_compute_backend_service.sovereign_public.id
    }
  }
}

resource "google_compute_url_map" "http_redirect" {
  project = var.project_id
  name    = "god-view-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_https_proxy" "https_proxy" {
  project          = var.project_id
  name             = "god-view-https-proxy"
  url_map          = google_compute_url_map.edge.id
  ssl_certificates = [google_compute_managed_ssl_certificate.edge_cert.id]
}

resource "google_compute_global_forwarding_rule" "https" {
  project               = var.project_id
  name                  = "god-view-https-fw-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "443"
  ip_address            = google_compute_global_address.lb_ip.id
  target                = google_compute_target_https_proxy.https_proxy.id
  labels                = local.cymbal_labels
}

resource "google_compute_target_http_proxy" "http_proxy" {
  project = var.project_id
  name    = "god-view-http-proxy"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  project               = var.project_id
  name                  = "god-view-http-fw-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "80"
  ip_address            = google_compute_global_address.lb_ip.id
  target                = google_compute_target_http_proxy.http_proxy.id
  labels                = local.cymbal_labels
}

# --- Cloud DNS Records in Existing Zone ak-demos-com ---

data "google_dns_managed_zone" "ak_demos" {
  count   = var.lookup_dns_zone ? 1 : 0
  project = var.project_id
  name    = var.dns_zone_name
}

resource "google_dns_record_set" "edge_hosts" {
  for_each     = local.dns_records
  project      = var.project_id
  managed_zone = var.lookup_dns_zone ? data.google_dns_managed_zone.ak_demos[0].name : var.dns_zone_name
  name         = each.value
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.lb_ip.address]
}
