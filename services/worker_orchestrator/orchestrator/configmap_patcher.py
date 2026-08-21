import asyncio
import logging
from kubernetes import client, config as k8s_config

logger = logging.getLogger(__name__)


class ConfigMapPatcher:
    """
    Patches a single-purpose ConfigMap (one line per IP rule) used to drive
    Nginx behavior for a specific IP — either a hard "deny" (Tier 2
    blocklist) or a rate-limit "geo" entry (Tier 1 dynamic rate limit).
    One class, parameterized by `rule_template`; instantiate once per
    ConfigMap/purpose.

    Changes are debounced: add_rule/remove_rule only queue a change and
    return whether it's a net-new change. The actual Kubernetes API patch
    happens once, `debounce_seconds` after the first queued change (or
    immediately if flush() is called explicitly) — this coalesces bursts of
    per-IP changes (e.g. many IPs mitigated during an active attack) into a
    single ConfigMap patch + Nginx reload instead of one per IP.
    """

    def __init__(
        self,
        namespace: str,
        configmap_name: str,
        rule_template: str = "deny {ip};",
        debounce_seconds: float = 3.0,
    ) -> None:
        self.namespace        = namespace
        self.configmap_name   = configmap_name
        self.rule_template    = rule_template
        self.debounce_seconds = debounce_seconds
        self._load_k8s_config()
        self.v1 = client.CoreV1Api()
        self._pending: dict[str, str | None] = {}
        self._flush_handle = None

    def _load_k8s_config(self) -> None:
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            logger.info("Loaded local kubeconfig")

    def _get_current_data(self) -> dict[str, str]:
        try:
            cm = self.v1.read_namespaced_config_map(
                name=self.configmap_name,
                namespace=self.namespace,
            )
            return cm.data or {}
        except client.ApiException as e:
            logger.error("Failed to read ConfigMap: %s", e)
            return {}

    @staticmethod
    def _ip_key(ip: str) -> str:
        return ip.replace(".", "_")

    def _effective_data(self) -> dict[str, str]:
        """Current ConfigMap data with not-yet-flushed pending changes applied."""
        data = dict(self._get_current_data())
        for key, rule in self._pending.items():
            if rule is None:
                data.pop(key, None)
            else:
                data[key] = rule
        return data

    def add_rule(self, ip: str) -> bool:
        key = self._ip_key(ip)
        if key in self._effective_data():
            logger.info("Rule already exists for IP: %s", ip)
            return False

        self._pending[key] = self.rule_template.format(ip=ip)
        self._schedule_flush()
        logger.info(
            "Rule queued for IP: %s (ConfigMap=%s, flush in %.1fs)",
            ip, self.configmap_name, self.debounce_seconds,
        )
        return True

    def remove_rule(self, ip: str) -> bool:
        key = self._ip_key(ip)
        if key not in self._effective_data():
            logger.info("No rule found for IP: %s", ip)
            return False

        self._pending[key] = None
        self._schedule_flush()
        logger.info(
            "Rule removal queued for IP: %s (ConfigMap=%s, flush in %.1fs)",
            ip, self.configmap_name, self.debounce_seconds,
        )
        return True

    def _schedule_flush(self) -> None:
        if self._flush_handle is not None:
            return
        try:
            loop = asyncio.get_event_loop()
            self._flush_handle = loop.call_later(self.debounce_seconds, self.flush)
        except RuntimeError:
            logger.warning(
                "No running event loop — call flush() manually to apply "
                "queued ConfigMap changes"
            )

    def flush(self) -> None:
        pending, self._pending = self._pending, {}
        self._flush_handle = None

        if not pending:
            return

        data = self._get_current_data()
        for key, rule in pending.items():
            if rule is None:
                data.pop(key, None)
            else:
                data[key] = rule

        try:
            self.v1.patch_namespaced_config_map(
                name=self.configmap_name,
                namespace=self.namespace,
                body={"data": data},
            )
            logger.info(
                "ConfigMap %s flushed: %d change(s)",
                self.configmap_name, len(pending),
            )
        except client.ApiException as e:
            logger.error(
                "Failed to flush ConfigMap %s: %s", self.configmap_name, e
            )

    def count_rules(self) -> int:
        return len(self._get_current_data())
