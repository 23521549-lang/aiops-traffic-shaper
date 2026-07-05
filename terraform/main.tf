module "vpc" {
  source              = "./modules/vpc"
  project_name        = var.project_name
  environment         = var.environment
  vpc_cidr            = var.vpc_cidr
  public_subnet_cidrs = var.public_subnet_cidrs
  availability_zones  = var.availability_zones
}

module "security_group" {
  source           = "./modules/security_group"
  project_name     = var.project_name
  environment      = var.environment
  vpc_id           = module.vpc.vpc_id
  allowed_ssh_cidr = var.allowed_ssh_cidr
}

module "ec2" {
  source               = "./modules/ec2"
  project_name         = var.project_name
  environment          = var.environment
  region               = var.region
  master_instance_type = var.master_instance_type
  worker_instance_type = var.worker_instance_type
  worker_count         = var.worker_count
  key_name             = var.key_name
  subnet_id            = module.vpc.public_subnet_ids[0]
  master_sg_id         = module.security_group.master_sg_id
  worker_sg_id         = module.security_group.worker_sg_id
  k8s_version          = var.k8s_version
  pod_network_cidr     = var.pod_network_cidr
}

module "ecr" {
  source                = "./modules/ecr"
  project_name          = var.project_name
  environment           = var.environment
  services              = var.ecr_services
  image_retention_count = var.ecr_image_retention_count
}



module "github_oidc" {
  source       = "./modules/github-oidc"
  project_name = var.project_name
  environment  = var.environment
  github_repo  = var.github_repo
}
