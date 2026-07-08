#!/bin/bash
set -euo pipefail
exec > /var/log/worker-join.log 2>&1

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
log_info()  { echo "$LOG_PREFIX [INFO]  $*"; }
log_warn()  { echo "$LOG_PREFIX [WARN]  $*"; }
log_error() { echo "$LOG_PREFIX [ERROR] $*" >&2; }
log_step()  { echo "$LOG_PREFIX [STEP]  -------- $* --------"; }

log_step "1/5 System preparation"
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

log_step "2/5 Install containerd"
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

log_step "3/5 Install kubeadm kubelet kubectl"
curl -fsSL https://pkgs.k8s.io/core:/stable:/v${k8s_version}/deb/Release.key \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${k8s_version}/deb/ /" \
  | tee /etc/apt/sources.list.d/kubernetes.list

apt-get update -y
apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl
systemctl enable kubelet
log_info "Kubernetes v${k8s_version} tools installed"

log_step "4/5 Retrieve join command from SSM"
JOIN_COMMAND=""
RETRY=0
MAX_RETRY=60
RETRY_INTERVAL=10

until [ -n "$JOIN_COMMAND" ] || [ "$RETRY" -ge "$MAX_RETRY" ]; do
  RETRY=$((RETRY + 1))
  log_info "Attempt $RETRY/$MAX_RETRY - fetching join command from SSM"

  JOIN_COMMAND=$(aws ssm get-parameter \
    --name "/${project_name}/k8s/join-command" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text \
    --region "${region}" 2>/dev/null || true)

  if [ -z "$JOIN_COMMAND" ]; then
    log_warn "Join command not available yet, retrying in $${RETRY_INTERVAL}s"
    sleep "$RETRY_INTERVAL"
  fi
done

if [ -z "$JOIN_COMMAND" ]; then
  log_error "Failed to retrieve join command after $MAX_RETRY attempts"
  exit 1
fi
log_info "Join command retrieved successfully"

log_step "5/5 Join Kubernetes cluster"
eval "$JOIN_COMMAND"
log_info "Worker node joined cluster successfully"