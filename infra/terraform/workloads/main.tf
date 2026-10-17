data "terraform_remote_state" "platform" {
  count   = var.provider_mode == "gcp" ? 1 : 0
  backend = "gcs"
  config = {
    bucket = var.tfstate_bucket
    prefix = var.platform_state_prefix
  }
}

locals {
  is_gcp  = var.provider_mode == "gcp"
  is_kind = var.provider_mode == "kind"

  remote_platform_outputs = try(data.terraform_remote_state.platform[0].outputs, {})

  platform_outputs_path = "${path.module}/../platform/outputs.json"
  raw_platform_outputs = {
    for k, v in try(jsondecode(file(local.platform_outputs_path)), {}) : k => try(v.value, v)
  }

  # Kind-only defaults (never used when provider_mode == "gcp")
  kind_defaults = {
    gke_cluster_name        = "god-view-gke-cluster"
    subnet_cidr             = "10.10.0.0/20"
    psa_cidr                = "10.100.0.0/16"
    sql_private_ip          = "10.100.0.3"
    cbs_vm_internal_ip      = "10.10.0.20"
    model_armor_template_id = "projects/${var.project_id}/locations/${var.region}/templates/cymbal-text-guard"
    sovereign_service_url   = "http://host.docker.internal:8090"
    kyc_ilb_address_name    = "god-view-kyc-ilb-ip"
    kyc_ilb_address         = "10.10.0.10"
    los_ilb_address_name    = "god-view-los-ilb-ip"
    los_ilb_address         = "10.10.0.11"
  }

  gke_cluster_name = try(
    coalesce(
      var.gke_cluster_name,
      local.is_kind ? local.kind_defaults.gke_cluster_name : null,
      try(tostring(local.remote_platform_outputs.gke_cluster_name), null),
      try(tostring(local.raw_platform_outputs.gke_cluster_name), null)
    ),
    null
  )

  gke_dns_endpoint = try(
    coalesce(
      var.gke_dns_endpoint != "" ? var.gke_dns_endpoint : null,
      local.is_kind ? null : try(tostring(local.remote_platform_outputs.gke_dns_endpoint), null),
      local.is_kind ? null : try(tostring(local.raw_platform_outputs.gke_dns_endpoint), null)
    ),
    ""
  )

  subnet_cidr = try(
    coalesce(
      var.subnet_cidr,
      local.is_kind ? local.kind_defaults.subnet_cidr : null,
      try(tostring(local.remote_platform_outputs.subnet_cidr), null),
      try(tostring(local.raw_platform_outputs.subnet_cidr), null)
    ),
    null
  )

  psa_cidr = try(
    coalesce(
      var.psa_range_cidr,
      var.psa_cidr,
      local.is_kind ? local.kind_defaults.psa_cidr : null,
      try(tostring(local.remote_platform_outputs.psa_range_cidr), null),
      try(tostring(local.remote_platform_outputs.psa_cidr), null),
      try(tostring(local.remote_platform_outputs.psa_range), null),
      try(tostring(local.raw_platform_outputs.psa_range_cidr), null),
      try(tostring(local.raw_platform_outputs.psa_cidr), null),
      try(tostring(local.raw_platform_outputs.psa_range), null)
    ),
    null
  )

  sql_private_ip = try(
    coalesce(
      var.sql_private_ip,
      local.is_kind ? local.kind_defaults.sql_private_ip : null,
      try(tostring(local.remote_platform_outputs.sql_private_ip), null),
      try(tostring(local.raw_platform_outputs.sql_private_ip), null)
    ),
    null
  )

  cbs_vm_internal_ip = try(
    coalesce(
      var.cbs_vm_internal_ip,
      local.is_kind ? local.kind_defaults.cbs_vm_internal_ip : null,
      try(tostring(local.remote_platform_outputs.cbs_vm_internal_ip), null),
      try(tostring(local.raw_platform_outputs.cbs_vm_internal_ip), null)
    ),
    null
  )

  model_armor_template_id = try(
    coalesce(
      var.model_armor_template_id,
      local.is_kind ? local.kind_defaults.model_armor_template_id : null,
      try(tostring(local.remote_platform_outputs.model_armor_template_id), null),
      try(tostring(local.raw_platform_outputs.model_armor_template_id), null)
    ),
    null
  )

  sovereign_service_url = try(
    coalesce(
      var.sovereign_service_url,
      local.is_kind ? local.kind_defaults.sovereign_service_url : null,
      try(tostring(local.remote_platform_outputs.sovereign_service_url), null),
      try(tostring(local.remote_platform_outputs.sovereign_uri), null),
      try(tostring(local.raw_platform_outputs.sovereign_service_url), null),
      try(tostring(local.raw_platform_outputs.sovereign_uri), null)
    ),
    null
  )

  kyc_ilb_address_name = try(
    coalesce(
      var.kyc_ilb_address_name,
      try(tostring(var.ilb_addresses.kyc_address_name), null),
      try(tostring(var.ilb_addresses.kyc_name), null),
      local.is_kind ? local.kind_defaults.kyc_ilb_address_name : null,
      try(tostring(local.remote_platform_outputs.ilb_addresses.kyc_address_name), null),
      try(tostring(local.remote_platform_outputs.ilb_addresses.kyc_name), null),
      try(tostring(local.remote_platform_outputs.kyc_ilb_address_name), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.kyc_address_name), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.kyc_name), null),
      try(tostring(local.raw_platform_outputs.kyc_ilb_address_name), null)
    ),
    null
  )

  kyc_ilb_address = try(
    coalesce(
      var.kyc_ilb_address,
      try(tostring(var.ilb_addresses.kyc_ip), null),
      try(tostring(var.ilb_addresses.kyc), null),
      try(tostring(var.ilb_addresses["kyc-model-armor-svc"]), null),
      local.is_kind ? local.kind_defaults.kyc_ilb_address : null,
      try(tostring(local.remote_platform_outputs.ilb_addresses.kyc_ip), null),
      try(tostring(local.remote_platform_outputs.ilb_addresses.kyc), null),
      try(tostring(local.remote_platform_outputs.kyc_ilb_address), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.kyc_ip), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.kyc), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses["kyc-model-armor-svc"]), null),
      try(tostring(local.raw_platform_outputs.kyc_ilb_address), null)
    ),
    null
  )

  los_ilb_address_name = try(
    coalesce(
      var.los_ilb_address_name,
      try(tostring(var.ilb_addresses.los_address_name), null),
      try(tostring(var.ilb_addresses.los_name), null),
      local.is_kind ? local.kind_defaults.los_ilb_address_name : null,
      try(tostring(local.remote_platform_outputs.ilb_addresses.los_address_name), null),
      try(tostring(local.remote_platform_outputs.ilb_addresses.los_name), null),
      try(tostring(local.remote_platform_outputs.los_ilb_address_name), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.los_address_name), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.los_name), null),
      try(tostring(local.raw_platform_outputs.los_ilb_address_name), null)
    ),
    null
  )

  los_ilb_address = try(
    coalesce(
      var.los_ilb_address,
      try(tostring(var.ilb_addresses.los_ip), null),
      try(tostring(var.ilb_addresses.los), null),
      try(tostring(var.ilb_addresses["underwriting-los-svc"]), null),
      local.is_kind ? local.kind_defaults.los_ilb_address : null,
      try(tostring(local.remote_platform_outputs.ilb_addresses.los_ip), null),
      try(tostring(local.remote_platform_outputs.ilb_addresses.los), null),
      try(tostring(local.remote_platform_outputs.los_ilb_address), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.los_ip), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses.los), null),
      try(tostring(local.raw_platform_outputs.ilb_addresses["underwriting-los-svc"]), null),
      try(tostring(local.raw_platform_outputs.los_ilb_address), null)
    ),
    null
  )

  common_labels = {
    godview_managed = "true"
    tenant          = "cymbal"
    tf_module       = "workloads"
  }

  effective_service_type = local.is_kind && var.service_type == "LoadBalancer" ? "NodePort" : var.service_type

  utilities = {
    "kyc-model-armor-svc" = {
      ksa_name         = kubernetes_service_account_v1.kyc.metadata[0].name
      ilb_address_name = local.kyc_ilb_address_name
      ilb_ip           = local.kyc_ilb_address
      node_port        = 30081
    }
    "underwriting-los-svc" = {
      ksa_name         = kubernetes_service_account_v1.los.metadata[0].name
      ilb_address_name = local.los_ilb_address_name
      ilb_ip           = local.los_ilb_address
      node_port        = 30082
    }
  }
}

resource "kubernetes_namespace_v1" "cymbal" {
  metadata {
    name   = "cymbal"
    labels = local.common_labels
  }

  lifecycle {
    precondition {
      condition     = local.gke_cluster_name != null && local.gke_cluster_name != ""
      error_message = "Missing required gke_cluster_name output from data.terraform_remote_state.platform or variables."
    }
  }
}

resource "kubernetes_config_map_v1" "tenant" {
  metadata {
    name      = "cymbal-tenant-config"
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels    = local.common_labels
  }

  data = {
    GODVIEW_ENV                 = local.is_kind ? "local" : "gcp"
    GCP_PROJECT_ID              = var.project_id
    GCP_REGION                  = var.region
    DB_HOST                     = local.sql_private_ip
    DB_PORT                     = "5432"
    DB_NAME                     = "godview_lms"
    DB_USER                     = "app_user"
    DB_PASSWORD_SECRET          = "cymbal-pg-app-password"
    DB_PASSWORD_SECRET_ID       = "projects/${var.project_id}/secrets/cymbal-pg-app-password/versions/latest"
    CBS_VM_HOST                 = local.cbs_vm_internal_ip
    CBS_VM_PORT                 = "8085"
    CBS_VM_URL                  = "http://${local.cbs_vm_internal_ip}:8085"
    MODEL_ARMOR_TEMPLATE_ID     = local.model_armor_template_id
    KYC_VAULT_BUCKET            = "general-ak-god-view-kyc-vault"
    OTEL_EXPORTER_OTLP_ENDPOINT = "${local.sovereign_service_url}/v1/traces"
  }

  lifecycle {
    precondition {
      condition = (
        local.sql_private_ip != null && local.sql_private_ip != "" &&
        local.cbs_vm_internal_ip != null && local.cbs_vm_internal_ip != "" &&
        local.model_armor_template_id != null && local.model_armor_template_id != "" &&
        local.sovereign_service_url != null && local.sovereign_service_url != ""
      )
      error_message = "Missing required platform outputs (sql_private_ip, cbs_vm_internal_ip, model_armor_template_id, or sovereign_service_url) from data.terraform_remote_state.platform / variables in gcp mode."
    }
  }
}

resource "kubernetes_service_account_v1" "kyc" {
  metadata {
    name      = "kyc-model-armor-svc"
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "iam.gke.io/gcp-service-account" = "godview-runtime-sa@${var.project_id}.iam.gserviceaccount.com"
    }
  }
}

resource "kubernetes_service_account_v1" "los" {
  metadata {
    name      = "underwriting-los-svc"
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels    = local.common_labels
    annotations = {
      "iam.gke.io/gcp-service-account" = "godview-runtime-sa@${var.project_id}.iam.gserviceaccount.com"
    }
  }
}

resource "kubernetes_deployment_v1" "utility" {
  for_each = local.utilities

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels = merge(local.common_labels, {
      app = each.key
    })
  }

  spec {
    replicas = var.hibernate ? 0 : var.gke_replicas

    selector {
      match_labels = {
        app = each.key
      }
    }

    template {
      metadata {
        labels = merge(local.common_labels, {
          app = each.key
        })
      }

      spec {
        service_account_name = each.value.ksa_name

        container {
          name              = each.key
          image             = lookup(var.images, each.key, "us-docker.pkg.dev/cloudrun/container/hello")
          image_pull_policy = local.is_kind ? "IfNotPresent" : "Always"

          port {
            name           = "http"
            container_port = 8080
            protocol       = "TCP"
          }

          env {
            name  = "SERVICE_NAME"
            value = each.key
          }

          env {
            name  = "PORT"
            value = "8080"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map_v1.tenant.metadata[0].name
            }
          }

          resources {
            requests = {
              cpu    = var.gke_pod_cpu
              memory = var.gke_pod_memory
            }
            limits = {
              cpu    = var.gke_pod_cpu
              memory = var.gke_pod_memory
            }
          }

          liveness_probe {
            http_get {
              path = "/healthz"
              port = 8080
            }
            initial_delay_seconds = 5
            period_seconds        = 10
            timeout_seconds       = 3
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/healthz"
              port = 8080
            }
            initial_delay_seconds = 3
            period_seconds        = 5
            timeout_seconds       = 2
            failure_threshold     = 3
          }
        }
      }
    }
  }

  wait_for_rollout = false
}

resource "kubernetes_service_v1" "utility" {
  for_each = local.utilities

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels = merge(local.common_labels, {
      app = each.key
    })
    annotations = merge(
      {
        "networking.gke.io/load-balancer-type" = "Internal"
      },
      contains(["annotation", "both"], var.ilb_ip_mode) ? {
        "networking.gke.io/load-balancer-ip-addresses" = each.value.ilb_address_name
      } : {}
    )
  }

  spec {
    type = local.effective_service_type

    load_balancer_ip = (
      local.effective_service_type == "LoadBalancer" && contains(["spec_ip", "both"], var.ilb_ip_mode)
      ? each.value.ilb_ip
      : null
    )

    selector = {
      app = each.key
    }

    port {
      name        = "http"
      port        = 8080
      target_port = 8080
      protocol    = "TCP"
      node_port   = local.effective_service_type == "NodePort" ? each.value.node_port : null
    }
  }

  wait_for_load_balancer = false

  lifecycle {
    precondition {
      condition = (
        each.value.ilb_address_name != null && each.value.ilb_address_name != "" &&
        each.value.ilb_ip != null && each.value.ilb_ip != ""
      )
      error_message = "Missing required ILB outputs (ilb_address_name / ilb_ip) for ${each.key} from data.terraform_remote_state.platform / variables in gcp mode."
    }
  }
}

resource "kubernetes_network_policy_v1" "utility" {
  for_each = local.utilities

  metadata {
    name      = "${each.key}-netpol"
    namespace = kubernetes_namespace_v1.cymbal.metadata[0].name
    labels = merge(local.common_labels, {
      app = each.key
    })
  }

  spec {
    pod_selector {
      match_labels = {
        app = each.key
      }
    }

    policy_types = ["Ingress", "Egress"]

    # Ingress only from god-view-subnet CIDR and Google health-checker ranges on TCP 8080
    ingress {
      ports {
        port     = 8080
        protocol = "TCP"
      }

      from {
        ip_block {
          cidr = local.subnet_cidr
        }
      }

      from {
        ip_block {
          cidr = "130.211.0.0/22"
        }
      }

      from {
        ip_block {
          cidr = "35.191.0.0/16"
        }
      }
    }

    # Egress to core-banking-cibil-vm in subnet on TCP 8085
    egress {
      ports {
        port     = 8085
        protocol = "TCP"
      }

      to {
        ip_block {
          cidr = local.subnet_cidr
        }
      }
    }

    # Egress to Cloud SQL private IP in PSA range on TCP 5432
    egress {
      ports {
        port     = 5432
        protocol = "TCP"
      }

      to {
        ip_block {
          cidr = local.psa_cidr
        }
      }
    }

    # Egress to GKE Workload Identity metadata server (GKE >=1.21 WI) on TCP 988
    egress {
      ports {
        port     = 988
        protocol = "TCP"
      }

      to {
        ip_block {
          cidr = "169.254.169.252/32"
        }
      }
    }

    # Egress to GCE/GKE metadata server (Dataplane V2 / Autopilot) on TCP 80
    egress {
      ports {
        port     = 80
        protocol = "TCP"
      }

      to {
        ip_block {
          cidr = "169.254.169.254/32"
        }
      }
    }

    # Egress to Google APIs (Model Armor, Cloud Trace, Cloud Monitoring, Secret Manager, GCS) on TCP 443
    egress {
      ports {
        port     = 443
        protocol = "TCP"
      }

      to {
        ip_block {
          cidr = "0.0.0.0/0"
        }
      }
    }

    # Egress for cluster DNS resolution (UDP/TCP 53)
    egress {
      ports {
        port     = 53
        protocol = "UDP"
      }
      ports {
        port     = 53
        protocol = "TCP"
      }
    }
  }

  lifecycle {
    precondition {
      condition = (
        local.subnet_cidr != null && local.subnet_cidr != "" &&
        local.psa_cidr != null && local.psa_cidr != ""
      )
      error_message = "Missing required network CIDR outputs (subnet_cidr / psa_range_cidr) from data.terraform_remote_state.platform / variables in gcp mode."
    }
  }
}
