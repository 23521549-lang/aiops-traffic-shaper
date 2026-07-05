variable "project_name" { type = string }
variable "environment"  { type = string }
variable "services" {
  description = "List of service names to create ECR repositories for"
  type        = list(string)
}
variable "image_retention_count" {
  description = "Number of images to retain per repository"
  type        = number
  default     = 10
}
