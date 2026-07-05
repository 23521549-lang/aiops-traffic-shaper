import logging
from kubernetes import client, config as k8s_config

logger = logging.getLogger(__name__)


class ConfigMapPatcher:
    def __init__(self, namespace: str, configmap_name: str) -> None:
        self.namespace = namespace
        self.configmap_name = configmap_name
        self._load_k8s_config()
        self.v1 = client.CoreV1Api()

    def _load_k8s_config(self) -> None:
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            logger.info("Loaded local kubeconfig")

    def _get_current_blocklist(self) -> dict[str, str]:
        try:
            cm = self.v1.read_namespaced_config_map(
                name=self.configmap_name,
                namespace=self.namespace,
            )
            return cm.data or {}
        except client.ApiException as e:
            logger.error("Failed to read ConfigMap: %s", e)
            return {}

    def add_deny_rule(self, ip: str) -> bool:
        data = self._get_current_blocklist()
        key = ip.replace(".", "_")

        if key in data:
            logger.info("Deny rule already exists for IP: %s", ip)
            return False

        data[key] = f"deny {ip};"

        try:
            self.v1.patch_namespaced_config_map(
                name=self.configmap_name,
                namespace=self.namespace,
                body={"data": data},
            )
            logger.info("Deny rule added for IP: %s", ip)
            return True
        except client.ApiException as e:
            logger.error("Failed to patch ConfigMap for IP %s: %s", ip, e)
            return False

    def remove_deny_rule(self, ip: str) -> bool:
        data = self._get_current_blocklist()
        key = ip.replace(".", "_")

        if key not in data:
            logger.info("No deny rule found for IP: %s", ip)
            return False

        del data[key]

        try:
            self.v1.patch_namespaced_config_map(
                name=self.configmap_name,
                namespace=self.namespace,
                body={"data": data},
            )
            logger.info("Deny rule removed for IP: %s", ip)
            return True
        except client.ApiException as e:
            logger.error("Failed to patch ConfigMap for IP %s: %s", ip, e)
            return False

    def count_rules(self) -> int:
        data = self._get_current_blocklist()
        return len(data)
