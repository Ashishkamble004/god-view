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
    tf_module       = "04-tenant-compute"
    tenant          = "cymbal"
  }

  cbs_startup_script = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p /opt/cibil-cbs /etc/default
    DB_PASS="$(gcloud secrets versions access latest --secret="${var.pg_password_secret_id}" --project="${var.project_id}" 2>/dev/null || true)"
    cat << ENVEOF > /etc/default/cibil-cbs
    GODVIEW_ENV=gcp
    GCP_PROJECT_ID=${var.project_id}
    DB_HOST=${var.sql_private_ip}
    DB_PORT=5432
    DB_NAME=${var.sql_database_name}
    DB_USER=app_user
    DB_PASSWORD=$${DB_PASS}
    DB_PASSWORD_SECRET=${var.pg_password_secret_id}
    DB_PASSWORD_SECRET_ID=projects/${var.project_id}/secrets/${var.pg_password_secret_id}/versions/latest
    OTEL_EXPORTER_OTLP_ENDPOINT=${var.sovereign_service_url}/v1/traces
    PORT=8085
    ENVEOF
    chmod 0640 /etc/default/cibil-cbs
    if gcloud storage cp gs://${var.kyc_vault_bucket}/vm-artifacts/cbs-payload.tar.gz /opt/cibil-cbs/payload.tar.gz 2>/dev/null || \
       gcloud storage cp gs://${var.kyc_vault_bucket}/vm-artifacts/core-banking-cibil-vm.tar.gz /opt/cibil-cbs/payload.tar.gz 2>/dev/null; then
      tar -xzf /opt/cibil-cbs/payload.tar.gz -C /opt/cibil-cbs
      if [ -f /opt/cibil-cbs/install.sh ]; then
        bash /opt/cibil-cbs/install.sh
        exit 0
      elif [ -x /opt/cibil-cbs/systemd/install.sh ]; then
        /opt/cibil-cbs/systemd/install.sh
        exit 0
      fi
    fi
    cat << 'PYEOF' > /opt/cibil-cbs/placeholder_server.py
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            body = json.dumps({
                "status": "ok",
                "service": "core-banking-cibil-vm",
                "mode": "placeholder",
                "cibil_score": 760,
                "disbursement_status": "DISBURSED"
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self._respond()
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 0:
                self.rfile.read(length)
            self._respond()

    if __name__ == "__main__":
        HTTPServer(("0.0.0.0", 8085), Handler).serve_forever()
    PYEOF
    pkill -f placeholder_server.py || true
    nohup python3 /opt/cibil-cbs/placeholder_server.py >/var/log/cbs-placeholder.log 2>&1 &
  EOT

  internal_invokers = toset([
    "serviceAccount:${var.runtime_sa_email}",
    "serviceAccount:${var.sense_sa_email}",
    "serviceAccount:${var.act_sa_email}",
  ])

  orchestrator_invokers = toset([
    "serviceAccount:${var.apigw_sa_email}",
    "serviceAccount:${var.runtime_sa_email}",
    "serviceAccount:${var.sense_sa_email}",
    "serviceAccount:${var.act_sa_email}",
  ])

  los_web_invokers = toset([
    "serviceAccount:service-${var.project_number}@gcp-sa-iap.iam.gserviceaccount.com",
    "serviceAccount:${var.sense_sa_email}",
  ])
}

# --- GKE Autopilot Cluster + Workload Identity ---

resource "google_container_cluster" "autopilot" {
  project             = var.project_id
  name                = "god-view-gke-cluster"
  location            = var.region
  enable_autopilot    = true
  network             = var.network_id
  subnetwork          = var.subnet_id
  deletion_protection = false
  resource_labels     = local.cymbal_labels

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {}
}

resource "google_service_account_iam_member" "gke_wi_kyc" {
  service_account_id = var.runtime_sa_name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[cymbal/kyc-model-armor-svc]"
  depends_on         = [google_container_cluster.autopilot]
}

resource "google_service_account_iam_member" "gke_wi_los" {
  service_account_id = var.runtime_sa_name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[cymbal/underwriting-los-svc]"
  depends_on         = [google_container_cluster.autopilot]
}

# --- GCE VM: core-banking-cibil-vm (Utility 5, e2-micro, no external IP) ---

resource "google_compute_instance" "cbs_vm" {
  project        = var.project_id
  name           = "core-banking-cibil-vm"
  machine_type   = var.vm_machine_type
  zone           = var.zone
  desired_status = var.hibernate ? "TERMINATED" : "RUNNING"
  labels         = local.cymbal_labels

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.vm_disk_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = var.subnet_id
    # Intentionally no access_config block: private IP only, egress via Cloud NAT
  }

  service_account {
    email  = var.runtime_sa_email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    GODVIEW_ENV                 = "gcp"
    OTEL_EXPORTER_OTLP_ENDPOINT = "${var.sovereign_service_url}/v1/traces"
    DB_HOST                     = var.sql_private_ip
    DB_PORT                     = "5432"
    DB_NAME                     = var.sql_database_name
    DB_USER                     = "app_user"
    DB_PASSWORD_SECRET_ID       = "projects/${var.project_id}/secrets/${var.pg_password_secret_id}/versions/latest"
    godview-env                 = "gcp"
    db-host                     = var.sql_private_ip
    db-port                     = "5432"
    db-name                     = var.sql_database_name
    db-user                     = "app_user"
    db-password-secret-id       = var.pg_password_secret_id
    kyc-vault-bucket            = var.kyc_vault_bucket
  }

  metadata_startup_script = local.cbs_startup_script
}

# --- Model Armor Template: cymbal-text-guard (text-only) ---

resource "google_model_armor_template" "text_guard" {
  project     = var.project_id
  location    = var.region
  template_id = "cymbal-text-guard"
  labels      = local.cymbal_labels

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "LOW_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "LOW_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "LOW_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "LOW_AND_ABOVE"
      }
    }
  }

  lifecycle {
    ignore_changes = [template_metadata]
  }
}

# --- 6 Tenant Cloud Run v2 Services ---

resource "google_cloud_run_v2_service" "commercial_fleet_credit" {
  project             = var.project_id
  name                = "commercial-fleet-credit-svc"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "commercial-fleet-credit-svc", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      env {
        name  = "GODVIEW_ENV"
        value = "gcp"
      }
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "${var.sovereign_service_url}/v1/traces"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "DB_HOST"
        value = var.sql_private_ip
      }
      env {
        name  = "DB_PORT"
        value = "5432"
      }
      env {
        name  = "DB_NAME"
        value = var.sql_database_name
      }
      env {
        name  = "DB_USER"
        value = "app_user"
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.pg_password_secret_id
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

resource "google_cloud_run_v2_service" "motor_insurance" {
  project             = var.project_id
  name                = "motor-insurance-svc"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "motor-insurance-svc", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      env {
        name  = "GODVIEW_ENV"
        value = "gcp"
      }
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "${var.sovereign_service_url}/v1/traces"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "DB_HOST"
        value = var.sql_private_ip
      }
      env {
        name  = "DB_PORT"
        value = "5432"
      }
      env {
        name  = "DB_NAME"
        value = var.sql_database_name
      }
      env {
        name  = "DB_USER"
        value = "app_user"
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.pg_password_secret_id
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

resource "google_cloud_run_v2_service" "loan_orchestrator" {
  project             = var.project_id
  name                = "loan-orchestrator-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "loan-orchestrator-api", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      env {
        name  = "GODVIEW_ENV"
        value = "gcp"
      }
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "${var.sovereign_service_url}/v1/traces"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "DB_HOST"
        value = var.sql_private_ip
      }
      env {
        name  = "DB_PORT"
        value = "5432"
      }
      env {
        name  = "DB_NAME"
        value = var.sql_database_name
      }
      env {
        name  = "DB_USER"
        value = "app_user"
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.pg_password_secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "KYC_SVC_URL"
        value = "http://${var.kyc_ilb_ip}:8080"
      }
      env {
        name  = "LOS_SVC_URL"
        value = "http://${var.los_ilb_ip}:8080"
      }
      env {
        name  = "UNDERWRITING_SVC_URL"
        value = "http://${var.los_ilb_ip}:8080"
      }
      env {
        name  = "FLEET_CREDIT_SVC_URL"
        value = google_cloud_run_v2_service.commercial_fleet_credit.uri
      }
      env {
        name  = "MOTOR_INSURANCE_SVC_URL"
        value = google_cloud_run_v2_service.motor_insurance.uri
      }
      env {
        name  = "INSURANCE_SVC_URL"
        value = google_cloud_run_v2_service.motor_insurance.uri
      }
      env {
        name  = "CBS_VM_URL"
        value = "http://${google_compute_instance.cbs_vm.network_interface[0].network_ip}:8085"
      }
      env {
        name  = "KYC_VAULT_BUCKET"
        value = var.kyc_vault_bucket
      }
      env {
        name  = "CDN_ASSETS_BUCKET"
        value = var.cdn_assets_bucket
      }
      env {
        name  = "BIGQUERY_LEDGER_DATASET"
        value = var.ledger_dataset_id
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

resource "google_cloud_run_v2_service" "customer_portal_web" {
  project              = var.project_id
  name                 = "customer-loan-portal-web"
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  invoker_iam_disabled = true
  deletion_protection  = false
  labels               = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "customer-loan-portal-web", "us-docker.pkg.dev/cloudrun/container/hello")

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

resource "google_cloud_run_v2_service" "internal_los_web" {
  project             = var.project_id
  name                = "internal-bank-los-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false
  labels              = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "internal-bank-los-web", "us-docker.pkg.dev/cloudrun/container/hello")

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

resource "google_cloud_run_v2_service" "ai_customer_simulator" {
  project             = var.project_id
  name                = "ai-customer-simulator"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false
  labels              = local.cymbal_labels

  template {
    service_account                  = var.runtime_sa_email
    max_instance_request_concurrency = var.cloud_run_concurrency

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    containers {
      image = lookup(var.images, "ai-customer-simulator", "us-docker.pkg.dev/cloudrun/container/hello")

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
      }

      env {
        name  = "GODVIEW_ENV"
        value = "gcp"
      }
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "${var.sovereign_service_url}/v1/traces"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "ORCHESTRATOR_URL"
        value = google_cloud_run_v2_service.loan_orchestrator.uri
      }
      env {
        name  = "KYC_VAULT_BUCKET"
        value = var.kyc_vault_bucket
      }
      env {
        name  = "GEMINI_LOCATION"
        value = "global"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# --- Cloud Run IAM Invokers (A2: no allUsers anywhere) ---

resource "google_cloud_run_v2_service_iam_member" "orchestrator_invokers" {
  for_each = local.orchestrator_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.loan_orchestrator.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "fleet_credit_invokers" {
  for_each = local.internal_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.commercial_fleet_credit.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "motor_insurance_invokers" {
  for_each = local.internal_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.motor_insurance.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "simulator_invokers" {
  for_each = local.internal_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ai_customer_simulator.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "portal_web_invokers" {
  for_each = local.internal_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.customer_portal_web.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "los_web_invokers" {
  for_each = local.los_web_invokers
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.internal_los_web.name
  role     = "roles/run.invoker"
  member   = each.value
}

# --- API Gateway: god-view-loan-api / god-view-loan-api-gw ---

resource "google_api_gateway_api" "loan_api" {
  provider     = google-beta
  project      = var.project_id
  api_id       = "god-view-loan-api"
  display_name = "Cymbal Auto Finance Loan API"
  labels       = local.cymbal_labels
}

resource "google_api_gateway_api_config" "loan_api_cfg" {
  provider             = google-beta
  project              = var.project_id
  api                  = google_api_gateway_api.loan_api.api_id
  api_config_id_prefix = "god-view-loan-cfg-"
  display_name         = "Cymbal Loan API Config"
  labels               = local.cymbal_labels

  openapi_documents {
    document {
      path = "openapi.yaml"
      contents = base64encode(
        templatefile(
          fileexists("${path.module}/../../../../api-gateway/openapi-spec.yaml.tftpl") ? "${path.module}/../../../../api-gateway/openapi-spec.yaml.tftpl" : "${path.module}/openapi-spec.yaml.tftpl",
          {
            orchestrator_uri = google_cloud_run_v2_service.loan_orchestrator.uri
          }
        )
      )
    }
  }

  gateway_config {
    backend_config {
      google_service_account = var.apigw_sa_email
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "loan_api_gw" {
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  gateway_id   = "god-view-loan-api-gw"
  api_config   = google_api_gateway_api_config.loan_api_cfg.id
  display_name = "Cymbal Loan API Gateway"
  labels       = local.cymbal_labels
}
