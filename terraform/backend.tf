terraform {
  backend "s3" {
    bucket         = "aiops-traffic-shaper-prod-terraform-state"
    key            = "terraform.tfstate"
    region         = "ap-southeast-1"
    dynamodb_table = "aiops-traffic-shaper-prod-terraform-lock"
    encrypt        = true
  }
}
