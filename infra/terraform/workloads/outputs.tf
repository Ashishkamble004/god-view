output "namespace" {
  description = "Kubernetes namespace hosting Cymbal GKE utilities."
  value       = kubernetes_namespace_v1.cymbal.metadata[0].name
}

output "service_accounts" {
  description = "Workload Identity Kubernetes ServiceAccount names."
  value = {
    kyc = kubernetes_service_account_v1.kyc.metadata[0].name
    los = kubernetes_service_account_v1.los.metadata[0].name
  }
}

output "deployments" {
  description = "Deployed GKE utility names and desired replicas."
  value = {
    for k, d in kubernetes_deployment_v1.utility : k => {
      name     = d.metadata[0].name
      replicas = d.spec[0].replicas
      image    = d.spec[0].template[0].spec[0].container[0].image
    }
  }
}

output "services" {
  description = "Internal LoadBalancer / NodePort service names and configured ILB addresses."
  value = {
    for k, s in kubernetes_service_v1.utility : k => {
      name             = s.metadata[0].name
      type             = s.spec[0].type
      ilb_address_name = local.utilities[k].ilb_address_name
      ilb_ip           = local.utilities[k].ilb_ip
    }
  }
}
