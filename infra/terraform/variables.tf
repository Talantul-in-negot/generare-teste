variable "project_id" {
  description = "GCP project that owns the platform resources."
  type        = string
}

variable "region" {
  description = "GCP region for the regional GKE cluster."
  type        = string
  default     = "europe-west3"
}

variable "cluster_name" {
  description = "Name of the regional GKE cluster."
  type        = string
  default     = "graphrag-prod"
}

variable "node_machine_type" {
  description = "Machine type for the application node pool."
  type        = string
  default     = "e2-standard-4"
}

variable "min_nodes" {
  description = "Minimum number of application nodes across the regional pool."
  type        = number
  default     = 3
}

variable "max_nodes" {
  description = "Maximum number of application nodes across the regional pool."
  type        = number
  default     = 9
}

variable "backup_retention_days" {
  description = "Days to retain tenant graph backup objects in Cloud Storage."
  type        = number
  default     = 35
}
