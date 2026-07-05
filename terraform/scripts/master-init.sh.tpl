#!/bin/bash
set -euo pipefail
exec > /var/log/master-init.log 2>&1

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

log_step "1/6 System preparation"
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gpg awscli
log_info "Packages installed"

swapoff -a
sed -i '/ swap / s/^/#/' /etc/fstab
log_info "Swap disabled"

printf 'overlay\nbr_netfilter\n' | tee /etc/modules-load.d/k8s.conf
modprobe overlay
modprobe br_netfilter
log_info "Kernel modules loaded"

printf 'net.bridge.bridge-nf-call-iptables  = 1\nnet.bridge.bridge-nf-call-ip6tables = 1\nnet.ipv4.ip_forward = 1\n' \
  | tee /etc/sysctl.d/k8s.conf
sysctl --system
log_info "Sysctl parameters applied"

log_step "2/6 Install containerd"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | tee /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y containerd.io

mkdir -p /etc/containerd
containerd config default | tee /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd
log_info "containerd installed and configured"

log_step "3/6 Install kubeadm kubelet kubectl"
curl -fsSL https://pkgs.k8s.io/core:/stable:/v${k8s_version}/deb/Release.key \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${k8s_version}/deb/ /" \
  | tee /etc/apt/sources.list.d/kubernetes.list

apt-get update -y
apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl
systemctl enable kubelet
log_info "Kubernetes v${k8s_version} tools installed"

log_step "4/6 Initialize cluster with kubeadm"
MASTER_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)
log_info "Detected master public IP: $MASTER_IP"

kubeadm init \
  --pod-network-cidr="${pod_network_cidr}" \
  --apiserver-advertise-address="$MASTER_IP" \
  --kubernetes-version="v${k8s_version}.0"
log_info "kubeadm init completed"

log_step "5/6 Configure kubeconfig"
mkdir -p /home/ubuntu/.kube
cp /etc/kubernetes/admin.conf /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config
log_info "kubeconfig configured for user ubuntu"

log_step "6/6 Install Flannel CNI and push join token to SSM"
export KUBECONFIG=/etc/kubernetes/admin.conf

kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml
log_info "Flannel CNI applied"

JOIN_COMMAND=$(kubeadm token create --print-join-command)

aws ssm put-parameter \
  --name "/${project_name}/k8s/join-command" \
  --value "$JOIN_COMMAND" \
  --type "SecureString" \
  --overwrite \
  --region "${region}"
log_info "Join command stored in SSM: /${project_name}/k8s/join-command"

log_info "Master node initialization completed successfully"