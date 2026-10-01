#!/usr/bin/env python3
"""Drive REAL iptables with the agent's own IptablesAdapter.

    wsl unshare --net --map-root-user python3 scripts/iptables-enforcement-drill.py
    # or, on Linux:
    unshare --net --map-root-user python3 scripts/iptables-enforcement-drill.py

Like the nginx drill beside it, every unit test of this adapter injects a fake
`subprocess.run`, so what is proven is the argv it builds. Whether netfilter
accepts that argv, and whether the rule stops a packet, was never exercised:
`docs/retrospective.md` carried "no real iptables has ever been driven by the
agent's enforcers" from Phase 7 until this script.

It needs no sudo and cannot touch the host's firewall. `unshare --net
--map-root-user` puts the process in its own network namespace, where it is
root and where the filter table starts empty. Every rule this adds is created
and destroyed inside that namespace; the host's INPUT chain is never opened.

Run it OUTSIDE the namespace and it refuses rather than asking for privileges
it should not have on a developer machine.
"""
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.agent.enforcer.iptables_adapter import IptablesAdapter  # noqa: E402

PORT = 8098
results = []


class _Quiet(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def check(label, got, want):
    ok = got == want
    results.append((ok, label))
    print("  %s  %s: got %r, want %r"
          % ("PASS" if ok else "FAIL", label, got, want))
    return ok


def reachable(timeout=2.0):
    """Whether a TCP connection to the listener completes.

    DROP is silent, so a blocked connection TIMES OUT rather than being
    refused. That difference is the whole point of DROP over REJECT, and a
    drill that only looked for ConnectionRefused would report a working
    block as a failure.
    """
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except (socket.timeout, TimeoutError):
        return False
    except OSError:
        return False


def main():
    if os.geteuid() != 0:
        print("Not root. Re-run inside a private network namespace:\n"
              "  unshare --net --map-root-user python3 %s\n"
              "This drill will not ask for sudo: it is not worth touching a "
              "developer machine's real firewall to test an adapter."
              % " ".join(sys.argv))
        return 2

    # A fresh namespace has lo DOWN, so nothing is reachable and the first
    # check would pass for the wrong reason.
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True,
                   capture_output=True)

    server = HTTPServer(("127.0.0.1", PORT), _Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)

    try:
        adapter = IptablesAdapter()

        print("\n1. netfilter is reachable and the namespace starts clean")
        check("adapter.is_available()", adapter.is_available(), True)
        rules = subprocess.run(["iptables", "-S", "INPUT"],
                               capture_output=True, text=True).stdout
        check("no aiops rule yet", "aiops-agent" in rules, False)
        check("connection before any block", reachable(), True)

        print("\n2. a real tier-2 block, through real iptables")
        check("adapter.block() reported success",
              adapter.block("127.0.0.1", tier=2,
                            expires_at=int(time.time()) + 3600), True)
        rules = subprocess.run(["iptables", "-S", "INPUT"],
                               capture_output=True, text=True).stdout
        print("    --- iptables -S INPUT ---")
        for line in rules.splitlines():
            print("    |" + line)
        check("rule carries the agent's comment", "aiops-agent" in rules, True)
        check("rule is a DROP", "-j DROP" in rules, True)
        check("connection while blocked", reachable(), False)

        print("\n3. unblocking removes the rule and restores the connection")
        check("adapter.unblock() reported success",
              adapter.unblock("127.0.0.1"), True)
        rules = subprocess.run(["iptables", "-S", "INPUT"],
                               capture_output=True, text=True).stdout
        check("rule is gone", "aiops-agent" in rules, False)
        check("connection after unblock", reachable(), True)

        print("\n4. tier 1 is refused rather than silently pretended")
        check("block(tier=1) returns False",
              adapter.block("203.0.113.9", tier=1, expires_at=0), False)
        rules = subprocess.run(["iptables", "-S", "INPUT"],
                               capture_output=True, text=True).stdout
        check("and wrote no rule", "203.0.113.9" in rules, False)

        print("\n5. garbage never reaches the shell")
        try:
            adapter.block("1.2.3.4 -j ACCEPT", tier=2, expires_at=0)
            check("injection attempt refused", False, True)
        except ValueError:
            check("injection attempt refused", True, True)
        rules = subprocess.run(["iptables", "-S", "INPUT"],
                               capture_output=True, text=True).stdout
        check("no ACCEPT rule appeared", "-j ACCEPT" in rules, False)
    finally:
        server.shutdown()

    failed = [r for r in results if not r[0]]
    print("\n=== REAL IPTABLES DRILL: %d/%d passed ==="
          % (len(results) - len(failed), len(results)))
    for ok, label in failed:
        print("  failed: " + label)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
