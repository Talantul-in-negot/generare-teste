output "artifact_registry" {
  value       = google_artifact_registry_repository.containers.name
  description = "Artifact Registry repository for application images."
}

output "cluster_name" {
  value       = google_container_cluster.primary.name
  description = "GKE cluster name."
}

output "cluster_region" {
  value       = google_container_cluster.primary.location
  description = "GKE cluster region."
}

output "backup_bucket_uri" {
  value       = "gs://${google_storage_bucket.backups.name}"
  description = "Versioned Cloud Storage location for tenant graph backups."
}
