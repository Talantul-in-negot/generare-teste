resource "google_project_service" "required" {
  for_each = toset([
    "container.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "graphrag"
  description   = "OCI images for AI Knowledge Graph Platform"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "backups" {
  name                        = "${var.project_id}-graphrag-backups"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = var.backup_retention_days }
  }
}

resource "google_service_account" "workload" {
  account_id   = "graphrag-workload"
  display_name = "GraphRAG application workload"
}

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[graphrag/graphrag-workload]"
}

resource "google_storage_bucket_iam_member" "backup_writer" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.workload.email}"
}

resource "google_container_cluster" "primary" {
  name                     = var.cluster_name
  location                 = var.region
  remove_default_node_pool = true
  initial_node_count       = 1

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {}

  depends_on = [google_project_service.required]
}

resource "google_container_node_pool" "application" {
  name       = "application"
  location   = var.region
  cluster    = google_container_cluster.primary.name
  node_count = var.min_nodes

  autoscaling {
    min_node_count = var.min_nodes
    max_node_count = var.max_nodes
  }

  node_config {
    machine_type    = var.node_machine_type
    service_account = google_service_account.workload.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}
