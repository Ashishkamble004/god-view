variable "project_id" {
  description = "GCP project ID hosting the GKE Autopilot cluster."
  type        = string
  default     = "general-ak"
}

variable "region" {
  description = "GCP region of the GKE Autopilot cluster."
  type        = string
  default     = "us-central1"
}

variable "tfstate_bucket" {
  description = "GCS bucket storing the platform and workloads Terraform remote states."
  type        = string
  default     = "general-ak-god-view-tfstate"
}

variable "platform_state_prefix" {
  description = "GCS state prefix for the platform Terraform root."
  type        = string
  default     = "platform"
}

variable "provider_mode" {
  description = "Target Kubernetes environment: 'gcp' (GKE Autopilot via google_client_config token) or 'kind' (local kind cluster)."
  type        = string
  default     = "gcp"
  validation {
    condition     = contains(["gcp", "kind"], var.provider_mode)
    error_message = "provider_mode must be either 'gcp' or 'kind'."
  }
}

variable "kube_context" {
  description = "Kubeconfig context when provider_mode is 'kind'."
  type        = string
  default     = "kind-godview"
}

variable "kubeconfig_path" {
  description = "Path to kubeconfig file when provider_mode is 'kind'."
  type        = string
  default     = "~/.kube/config"
}

variable "service_type" {
  description = "Kubernetes Service type for the GKE utilities ('LoadBalancer' on GKE, 'NodePort' on kind)."
  type        = string
  default     = "LoadBalancer"
}

variable "ilb_ip_mode" {
  description = "How internal LB IP is bound on GKE Services: 'annotation' (networking.gke.io/load-balancer-ip-addresses), 'spec_ip' (spec.load_balancer_ip fallback per Decision A4), or 'both'."
  type        = string
  default     = "annotation"
  validation {
    condition     = contains(["annotation", "spec_ip", "both"], var.ilb_ip_mode)
    error_message = "ilb_ip_mode must be 'annotation', 'spec_ip', or 'both'."
  }
}

variable "gke_cluster_name" {
  description = "Name of the GKE Autopilot cluster (read from platform remote state in gcp mode unless explicitly overridden)."
  type        = string
  default     = null
  nullable    = true
}

variable "gke_dns_endpoint" {
  description = "IAM-authenticated GKE control plane DNS endpoint from platform outputs (if empty, uses google_container_cluster.endpoint)."
  type        = string
  default     = null
  nullable    = true
}

variable "gke_pod_cpu" {
  description = "CPU request and limit for each GKE utility pod."
  type        = string
  default     = "250m"
}

variable "gke_pod_memory" {
  description = "Memory request and limit for each GKE utility pod."
  type        = string
  default     = "512Mi"
}

variable "gke_replicas" {
  description = "Desired replica count per GKE utility deployment when not hibernated."
  type        = number
  default     = 1
}

variable "hibernate" {
  description = "When true, scales GKE utility deployments to 0 replicas for cost floor hibernation."
  type        = bool
  default     = false
}

variable "subnet_cidr" {
  description = "CIDR block of god-view-subnet for NetworkPolicy ingress and VM egress (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "psa_cidr" {
  description = "CIDR block of Private Service Access range for Cloud SQL egress on TCP 5432 (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "psa_range_cidr" {
  description = "Alias for psa_cidr matching platform root output psa_range_cidr."
  type        = string
  default     = null
  nullable    = true
}

variable "kyc_ilb_address_name" {
  description = "Reserved internal address resource name for kyc-model-armor-svc ILB (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "kyc_ilb_address" {
  description = "Reserved internal IPv4 address for kyc-model-armor-svc ILB (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "los_ilb_address_name" {
  description = "Reserved internal address resource name for underwriting-los-svc ILB (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "los_ilb_address" {
  description = "Reserved internal IPv4 address for underwriting-los-svc ILB (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "ilb_addresses" {
  description = "Optional map of reserved internal LB addresses and resource names from platform root outputs."
  type        = any
  default     = {}
}

variable "sql_private_ip" {
  description = "Private IPv4 address of Cloud SQL instance god-view-pg (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "cbs_vm_internal_ip" {
  description = "Internal IPv4 address of core-banking-cibil-vm (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "model_armor_template_id" {
  description = "Model Armor template ID for text-only scanning in kyc-model-armor-svc (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "sovereign_service_url" {
  description = "Base URL of the God-View Sovereign service for OTLP trace span export (read from platform remote state in gcp mode)."
  type        = string
  default     = null
  nullable    = true
}

variable "images" {
  description = "Container image references for the two GKE utility services (pinned in images.auto.tfvars)."
  type        = map(string)
  default = {
    "kyc-model-armor-svc"  = "us-docker.pkg.dev/cloudrun/container/hello"
    "underwriting-los-svc" = "us-docker.pkg.dev/cloudrun/container/hello"
  }
}
