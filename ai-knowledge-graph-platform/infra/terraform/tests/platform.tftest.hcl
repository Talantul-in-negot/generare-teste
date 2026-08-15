mock_provider "google" {}

variables {
  project_id            = "test-project"
  region                = "europe-west3"
  cluster_name          = "graphrag-test"
  node_machine_type     = "e2-standard-4"
  min_nodes             = 3
  max_nodes             = 9
  backup_retention_days = 35
}

run "plan_uses_secure_platform_defaults" {
  command = plan

  assert {
    condition     = google_container_cluster.primary.networking_mode == "VPC_NATIVE"
    error_message = "The GKE cluster must use VPC-native networking."
  }

  assert {
    condition     = google_container_cluster.primary.release_channel[0].channel == "REGULAR"
    error_message = "The GKE cluster must use the regular release channel."
  }

  assert {
    condition     = google_storage_bucket.backups.uniform_bucket_level_access
    error_message = "Backups must use uniform bucket-level access."
  }

  assert {
    condition     = google_storage_bucket.backups.versioning[0].enabled
    error_message = "Backups must have object versioning enabled."
  }

  assert {
    condition     = google_container_node_pool.application.autoscaling[0].min_node_count == 3
    error_message = "The default application pool minimum must remain three nodes."
  }
}
