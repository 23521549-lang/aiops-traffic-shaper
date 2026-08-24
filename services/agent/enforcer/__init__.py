import logging
import time

from services.agent.enforcer.base import EnforcementAdapter
from services.agent.enforcer.iptables_adapter import IptablesAdapter
from services.agent.enforcer.nginx_adapter import NginxAdapter

logger = logging.getLogger(__name__)


def detect_adapters(candidates: list[EnforcementAdapter] | None = None) -> list[EnforcementAdapter]:
    """Auto-detects which adapters can actually run here, keeping every one
    that reports itself available — NOT a single fixed choice made at
    install time. Unlike a design where the operator picks exactly one
    enforcement mechanism up front, multiple adapters can be active
    simultaneously (e.g. nginx AND iptables both enforcing the same
    HARD_BLOCK decision) — defense in depth, and it works out of the box
    across more environments without per-install configuration."""
    candidates = candidates if candidates is not None else [NginxAdapter(), IptablesAdapter()]
    available = []
    for adapter in candidates:
        try:
            if adapter.is_available():
                available.append(adapter)
        except Exception as e:
            logger.warning("Adapter %s availability check raised, skipping: %s", adapter.name, e)
    return available


class DecisionStore:
    """Tracks currently-enforced mitigation decisions and their expiry, so
    the agent can unblock an IP once its TTL passes — closing the loop
    that a real (non-zero) `expires_at` from the backend exists for.
    Neither nginx's `deny` nor an iptables DROP rule expires on its own;
    something has to actively remove them later, which is this class's job."""

    def __init__(self):
        self._active: dict[str, dict] = {}

    def apply(self, decisions: list[dict], adapters: list[EnforcementAdapter]) -> None:
        for decision in decisions:
            ip = decision["ip"]
            self._active[ip] = decision
            for adapter in adapters:
                try:
                    ok = adapter.block(ip, decision["tier"], decision.get("expires_at", 0))
                    logger.info("%s: %s ip=%s tier=%d", adapter.name,
                                "applied" if ok else "not applicable", ip, decision["tier"])
                except Exception as e:
                    logger.error("%s failed to block ip=%s: %s", adapter.name, ip, e)

    def sweep_expired(self, adapters: list[EnforcementAdapter], now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        expired = [ip for ip, d in self._active.items()
                   if d.get("expires_at", 0) and d["expires_at"] <= now]
        for ip in expired:
            for adapter in adapters:
                try:
                    adapter.unblock(ip)
                except Exception as e:
                    logger.error("%s failed to unblock ip=%s: %s", adapter.name, ip, e)
            del self._active[ip]
        return expired

    def active_ips(self) -> list[str]:
        return list(self._active)
