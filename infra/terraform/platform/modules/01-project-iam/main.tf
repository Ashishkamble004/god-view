locals {
  godview_labels = {
    godview_managed = "true"
    tf_module       = "01-project-iam"
    tenant          = "godview"
    godview_role    = "observer"
  }

  apis = toset([
    "run.googleapis.com",
    "container.googleapis.com",
    "compute.googleapis.com",
    "sqladmin.googleapis.com",
    "firestore.googleapis.com",
    "bigquery.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com",
    "apigateway.googleapis.com",
    "servicecontrol.googleapis.com",
    "servicemanagement.googleapis.com",
    "aiplatform.googleapis.com",
    "modelarmor.googleapis.com",
    "cloudasset.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "secretmanager.googleapis.com",
    "iap.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "artifactregistry.googleapis.com",
    "servicenetworking.googleapis.com",
    "dns.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "datastream.googleapis.com",
    "certificatemanager.googleapis.com",
    "cloudbuild.googleapis.com",
  ])

  sense_project_roles = toset([
    "roles/aiplatform.user",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
    "roles/compute.viewer",
    "roles/run.viewer",
    "roles/pubsub.viewer",
    "roles/iam.securityReviewer",
    "roles/apigateway.viewer",
    "roles/container.viewer",
    "roles/cloudsql.viewer",
    "roles/cloudasset.viewer",
    "roles/datastore.user",
    "roles/bigquery.jobUser",
    "roles/bigquery.metadataViewer",
    "roles/secretmanager.viewer",
    "roles/cloudtrace.user",
  ])

  act_project_roles = toset([
    "roles/compute.securityAdmin",
    "roles/run.admin",
    "roles/pubsub.editor",
    "roles/container.developer",
  ])

  runtime_project_roles = toset([
    "roles/cloudsql.client",
    "roles/cloudtrace.agent",
    "roles/monitoring.metricWriter",
    "roles/logging.logWriter",
    "roles/modelarmor.user",
    "roles/aiplatform.user",
  ])

  deploy_project_roles = toset([
    "roles/compute.admin",
    "roles/container.admin",
    "roles/run.admin",
    "roles/cloudsql.admin",
    "roles/datastore.owner",
    "roles/bigquery.admin",
    "roles/storage.admin",
    "roles/secretmanager.admin",
    "roles/pubsub.admin",
    "roles/cloudscheduler.admin",
    "roles/monitoring.admin",
    "roles/dns.admin",
    "roles/apigateway.admin",
    "roles/iap.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/resourcemanager.projectIamAdmin",
    "roles/serviceusage.serviceUsageAdmin",
  ])
}

resource "google_project_service" "apis" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "containers" {
  project       = var.project_id
  location      = var.region
  repository_id = "god-view-containers"
  description   = "God-View and Cymbal Auto Finance container images"
  format        = "DOCKER"
  labels        = local.godview_labels

  cleanup_policies {
    id     = "keep-recent-10"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}

resource "google_service_account" "sense" {
  project      = var.project_id
  account_id   = "godview-sense-sa"
  display_name = "God-View Sovereign & Court Sense SA (Read-Only)"
  depends_on   = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_service_account" "act" {
  project      = var.project_id
  account_id   = "godview-act-sa"
  display_name = "God-View Scoped Remediation Act SA (Impersonated)"
  depends_on   = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "godview-runtime-sa"
  display_name = "Cymbal Auto Finance Tenant Runtime SA"
  depends_on   = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_service_account" "apigw" {
  project      = var.project_id
  account_id   = "godview-apigw-sa"
  display_name = "Cymbal API Gateway Backend OIDC SA"
  depends_on   = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "godview-deploy-sa"
  display_name = "God-View GitHub Actions WIF Deploy SA"
  depends_on   = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_project_iam_member" "sense_roles" {
  for_each = local.sense_project_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.sense.email}"
}

resource "google_project_iam_member" "act_roles" {
  for_each = local.act_project_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.act.email}"
}

resource "google_project_iam_custom_role" "act_hibernate" {
  project     = var.project_id
  role_id     = "godview_hibernate_operator"
  title       = "God-View Act SA Hibernate & Ops Role"
  description = "Scoped permissions for hibernate job and infrastructure control"
  permissions = [
    "compute.instances.get",
    "compute.instances.start",
    "compute.instances.stop",
    "compute.zoneOperations.get",
    "cloudsql.instances.get",
    "cloudsql.instances.update",
    "cloudscheduler.jobs.get",
    "cloudscheduler.jobs.pause",
    "cloudscheduler.jobs.enable",
    "cloudscheduler.jobs.run",
    "aiplatform.reasoningEngines.get",
    "aiplatform.reasoningEngines.list",
    "aiplatform.reasoningEngines.update",
  ]
}

resource "google_project_iam_member" "act_hibernate" {
  project = var.project_id
  role    = google_project_iam_custom_role.act_hibernate.id
  member  = "serviceAccount:${google_service_account.act.email}"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = local.runtime_project_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "deploy_roles" {
  for_each = local.deploy_project_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "sense_impersonate_act" {
  service_account_id = google_service_account.act.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.sense.email}"
}

resource "google_service_account_iam_member" "sense_self_token_creator" {
  service_account_id = google_service_account.sense.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.sense.email}"
}

resource "google_service_account_iam_member" "act_user_on_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.act.email}"
}

resource "google_service_account_iam_member" "re_sa_user_sense" {
  service_account_id = google_service_account.sense.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
  description               = "Workload Identity Federation pool for GitHub Actions CI/CD"
  depends_on                = [google_project_service.apis["iam.googleapis.com"], google_project_service.apis["sts.googleapis.com"]]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Actions OIDC Provider"
  description                        = "OIDC provider for GitHub Actions workflows"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "wif_deploy" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
