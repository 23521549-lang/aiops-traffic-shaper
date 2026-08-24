from abc import ABC, abstractmethod


class EnforcementAdapter(ABC):
    """One pluggable local enforcement backend. Multiple adapters can be
    active at once (see enforcer/__init__.py's detect_adapters) — this is
    deliberately NOT a single fixed mechanism chosen at install time
    (the gap in the simpler fail2ban/single-hook designs this project's
    enforcer improves on)."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Self-detects whether this adapter can actually act in the
        current environment (e.g. is nginx's config dir present? does the
        iptables binary exist and run?) — never assumed from config alone."""

    @abstractmethod
    def block(self, ip: str, tier: int, expires_at: int) -> bool:
        """Apply a mitigation decision for `ip`. Returns True on success,
        False if this adapter legitimately cannot express this decision
        (e.g. a soft rate-limit tier on an adapter that only supports hard
        blocks) — not an exception, since that's an expected, named
        limitation of some adapters, not a failure."""

    @abstractmethod
    def unblock(self, ip: str) -> bool:
        """Reverse a previously applied block — called once its
        expires_at has passed (see enforcer/__init__.py's DecisionStore)."""
