import subprocess
from pathlib import Path

from services.agent.enforcer.base import EnforcementAdapter

DEFAULT_CONFIG_DIR = Path("/etc/nginx/conf.d")

# tier 2 (HARD_BLOCK) -> a `deny <ip>;` include, one line per IP.
# tier 1 (RATE_LIMIT) -> a `geo` map feeding a `limit_req_zone` the
# tenant's own nginx.conf is expected to define against this variable —
# the standard, documented nginx pattern for a dynamically-updated,
# per-IP rate limit (there is no way to express a live-updatable
# per-IP soft limit as a single static directive the way `deny` works
# for a hard block).
_BLOCK_FILENAME = "aiops-agent-deny.conf"
_RATELIMIT_FILENAME = "aiops-agent-geo.conf"
_GEO_VAR = "$agent_rate_limited"


class NginxAdapter(EnforcementAdapter):
    name = "nginx"

    def __init__(self, config_dir: Path = DEFAULT_CONFIG_DIR,
                 reload_cmd: list[str] | None = None, run_command=subprocess.run):
        self._config_dir = Path(config_dir)
        self._reload_cmd = reload_cmd or ["nginx", "-s", "reload"]
        self._run_command = run_command
        self._block_file = self._config_dir / _BLOCK_FILENAME
        self._ratelimit_file = self._config_dir / _RATELIMIT_FILENAME

    def is_available(self) -> bool:
        return self._config_dir.exists()

    def _blocked_ips(self) -> set[str]:
        if not self._block_file.exists():
            return set()
        return {
            line.split()[1].rstrip(";")
            for line in self._block_file.read_text().splitlines()
            if line.startswith("deny ")
        }

    def _ratelimited_ips(self) -> set[str]:
        if not self._ratelimit_file.exists():
            return set()
        ips = set()
        for line in self._ratelimit_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("geo", "default", "}")):
                ips.add(line.split()[0])
        return ips

    def _write_block_file(self, ips: set[str]) -> None:
        self._block_file.write_text("".join(f"deny {ip};\n" for ip in sorted(ips)))

    def _write_ratelimit_file(self, ips: set[str]) -> None:
        body = "".join(f"    {ip} 1;\n" for ip in sorted(ips))
        self._ratelimit_file.write_text(
            f"geo $binary_remote_addr {_GEO_VAR} {{\n    default 0;\n{body}}}\n"
        )

    def block(self, ip: str, tier: int, expires_at: int) -> bool:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if tier == 2:
            self._write_block_file(self._blocked_ips() | {ip})
        else:
            self._write_ratelimit_file(self._ratelimited_ips() | {ip})
        return self._reload()

    def unblock(self, ip: str) -> bool:
        changed = False
        if ip in self._blocked_ips():
            self._write_block_file(self._blocked_ips() - {ip})
            changed = True
        if ip in self._ratelimited_ips():
            self._write_ratelimit_file(self._ratelimited_ips() - {ip})
            changed = True
        return self._reload() if changed else True

    def _reload(self) -> bool:
        result = self._run_command(self._reload_cmd, capture_output=True)
        return result.returncode == 0
