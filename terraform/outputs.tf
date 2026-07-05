output "master_public_ip" {
  description = "Public IP cua master node"
  value       = module.ec2.master_public_ip
}

output "worker_public_ips" {
  description = "Public IPs cua worker nodes"
  value       = module.ec2.worker_public_ips
}

output "ssh_to_master" {
  description = "Lenh SSH vao master node"
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ubuntu@${module.ec2.master_public_ip}"
}

output "get_kubeconfig" {
  description = "Lenh copy kubeconfig ve may local"
  value       = "scp -i ~/.ssh/${var.key_name}.pem ubuntu@${module.ec2.master_public_ip}:~/.kube/config ~/.kube/config"
}

output "ecr_repository_urls" {
  description = "ECR repository URLs cho tung service"
  value       = module.ecr.repository_urls
}

output "ecr_registry_id" {
  description = "ECR registry ID (AWS account ID)"
  value       = module.ecr.registry_id
}

output "docker_login_command" {
  description = "Lenh authenticate Docker voi ECR"
  value       = "aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${module.ecr.registry_id}.dkr.ecr.${var.region}.amazonaws.com"
}

output "github_actions_role_arn" {
  description = "IAM Role ARN cho GitHub Actions OIDC"
  value       = module.github_oidc.github_actions_role_arn
}
