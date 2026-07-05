region               = "ap-southeast-1"
project_name         = "aiops-traffic-shaper"
environment          = "prod"
vpc_cidr             = "10.0.0.0/16"
public_subnet_cidrs  = ["10.0.1.0/24", "10.0.2.0/24"]
availability_zones   = ["ap-southeast-1a", "ap-southeast-1b"]
master_instance_type = "t3.medium"
worker_instance_type = "t3.medium"
worker_count         = 2
key_name             = "aiops-keypair"
k8s_version          = "1.29"
pod_network_cidr     = "10.244.0.0/16"
allowed_ssh_cidr     = "0.0.0.0/0"

github_repo = "your-username/aiops-traffic-shaper"
