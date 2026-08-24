import ipaddress
import subprocess

from services.agent.enforcer.base import EnforcementAdapter

_COMMENT = "aiops-agent"


class IptablesAdapter(EnforcementAdapter):
    """OS-level blocking — works regardless of whatever app/proxy stack the
    tenant runs (no nginx dependency), the most universal adapter. Only
    handles tier 2 (HARD_BLOCK): a proportional soft rate-limit isn't
    cleanly expressible with plain iptables DROP rules (would need the
    less-portable hashlimit module) — block() returns False for tier 1
    rather than silently pretending to support it. Register an adapter
    that DOES support tier 1 (e.g. NginxAdapter) alongside this one if
    tier 1 enforcement matters — see enforcer/__init__.py."""

    name = "iptables"

    def __init__(self, run_command=subprocess.run, iptables_bin: str = "iptables"):
        self._run_command = run_command
        self._bin = iptables_bin

    def is_available(self) -> bool:
        try:
            result = self._run_command([self._bin, "--version"], capture_output=True)
        except FileNotFoundError:
            return False
        return result.returncode == 0

    def block(self, ip: str, tier: int, expires_at: int) -> bool:
        if tier != 2:
            return False  # not applicable — see class docstring
        ipaddress.ip_address(ip)  # raise ValueError on garbage before shelling out
        result = self._run_command(
            [self._bin, "-A", "INPUT", "-s", ip, "-m", "comment",
             "--comment", _COMMENT, "-j", "DROP"],
            capture_output=True,
        )
        return result.returncode == 0

    def unblock(self, ip: str) -> bool:
        result = self._run_command(
            [self._bin, "-D", "INPUT", "-s", ip, "-m", "comment",
             "--comment", _COMMENT, "-j", "DROP"],
            capture_output=True,
        )
        return result.returncode == 0
