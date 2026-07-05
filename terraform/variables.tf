variable "region" {
  description = "AWS region"
  type        = string
}

variable "project_name" {
  description = "Project name — dung cho tat ca resource naming"
  type        = string
}

variable "environment" {
  description = "Environment: dev | staging | prod"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block cho VPC"
  type        = string
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks cho public subnets"
  type        = list(string)
}

variable "availability_zones" {
  description = "Availability zones trong region"
  type        = list(string)
}

variable "master_instance_type" {
  description = "EC2 instance type cho master node"
  type        = string
}

variable "worker_instance_type" {
  description = "EC2 instance type cho worker nodes"
  type        = string
}

variable "worker_count" {
  description = "So luong worker nodes"
  type        = number
}

variable "key_name" {
  description = "AWS EC2 Key Pair name de SSH vao nodes"
  type        = string
}

variable "k8s_version" {
  description = "Kubernetes version cai dat"
  type        = string
}

variable "pod_network_cidr" {
  description = "CIDR cho pod network (Flannel)"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR duoc phep SSH vao nodes"
  type        = string
}

variable "ecr_services" {
  description = "List of service names to create ECR repositories for"
  type        = list(string)
  default     = ["ai-engine", "worker-orchestrator"]
}

variable "ecr_image_retention_count" {
  description = "Number of images to retain per ECR repository"
  type        = number
  default     = 10
}

variable "github_repo" {
  description = "GitHub repository in format owner/repo (e.g. username/aiops-traffic-shaper)"
  type        = string
}
