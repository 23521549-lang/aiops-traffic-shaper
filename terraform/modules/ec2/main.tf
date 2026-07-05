data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_iam_role" "master" {
  name = "${var.project_name}-${var.environment}-master-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "master_ssm" {
  name = "ssm-put-join-token"
  role = aws_iam_role.master.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:PutParameter", "ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.region}:*:parameter/${var.project_name}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "master" {
  name = "${var.project_name}-${var.environment}-master-profile"
  role = aws_iam_role.master.name
}

resource "aws_iam_role" "worker" {
  name = "${var.project_name}-${var.environment}-worker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "worker_ssm" {
  name = "ssm-get-join-token"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.region}:*:parameter/${var.project_name}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.project_name}-${var.environment}-worker-profile"
  role = aws_iam_role.worker.name
}

resource "aws_instance" "master" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.master_instance_type
  key_name               = var.key_name
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.master_sg_id]
  iam_instance_profile   = aws_iam_instance_profile.master.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/../../scripts/master-init.sh.tpl", {
    k8s_version      = var.k8s_version
    pod_network_cidr = var.pod_network_cidr
    project_name     = var.project_name
    region           = var.region
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-master"
    Role = "master"
  }
}

resource "aws_instance" "worker" {
  count                  = var.worker_count
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.worker_instance_type
  key_name               = var.key_name
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.worker_sg_id]
  iam_instance_profile   = aws_iam_instance_profile.worker.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/../../scripts/worker-join.sh.tpl", {
    k8s_version  = var.k8s_version
    project_name = var.project_name
    region       = var.region
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-worker-${count.index + 1}"
    Role = "worker"
  }

  depends_on = [aws_instance.master]
}
