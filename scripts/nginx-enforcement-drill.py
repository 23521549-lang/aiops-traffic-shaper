#!/usr/bin/env python3
"""Drive a REAL nginx with the agent's own NginxAdapter.

    python3 scripts/nginx-enforcement-drill.py [--port 8099]

Every unit test of this adapter injects a fake `run_command`. What they prove
is that the right bytes land on disk and the right argv *would* have been
executed. Whether nginx accepts those files, and whether a reload changes what
a client actually receives, was never exercised: `docs/retrospective.md`
carried "no real nginx has ever been driven by the agent's enforcers" from
Phase 7 until this script.

It needs no root and modifies nothing the system owns. nginx runs unprivileged
on a high port out of a temporary prefix that is deleted afterwards. The only
requirement is an `nginx` binary on PATH.

Two things it is careful about, both of which produced a false result first:

  - The test server serves a FILE, not `return 200`. `return` is handled in
    nginx's rewrite phase, which runs BEFORE the access phase where `deny`
    lives, so a `return` server answers 200 with the deny rule sitting right
    above it and the drill reports that blocking does not work.

  - The filenames and the geo variable are read from the adapter module, never
    restated. Spelling them out by hand pointed the assertions at files the
    adapter never writes, and the drill passed while proving nothing.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.agent.enforcer import nginx_adapter as NA  # noqa: E402
from services.agent.enforcer.nginx_adapter import NginxAdapter  # noqa: E402

BLOCK_FILE = NA._BLOCK_FILENAME
GEO_FILE = NA._RATELIMIT_FILENAME
GEO_VAR = NA._GEO_VAR

_CONF = """
pid {p}/nginx.pid;
error_log {p}/logs/error.log;
events {{ worker_connections 64; }}
http {{
    access_log {p}/logs/access.log;
    client_body_temp_path {p}/body;
    proxy_temp_path {p}/proxy;
    fastcgi_temp_path {p}/fastcgi;
    uwsgi_temp_path {p}/uwsgi;
    scgi_temp_path {p}/scgi;

    include {d}/{geo};
    # The directive the adapter's own comment tells a tenant to pair the geo
    # map with. Including it means `nginx -t` validates the whole documented
    # pattern rather than only that the generated file parses.
    limit_req_zone $binary_remote_addr zone=aiops:1m rate=100r/s;

    server {{
        listen 127.0.0.1:{port};
        root {p}/www;
        location / {{
            include {d}/{block};
            limit_req zone=aiops burst=50 nodelay;
            index index.html;
        }}
    }}
}}
"""


class Drill:
    def __init__(self, port):
        self.port = port
        self.base = "http://127.0.0.1:%d/" % port
        self.results = []

    def check(self, label, got, want):
        ok = got == want
        self.results.append((ok, label))
        print("  %s  %s: got %r, want %r"
              % ("PASS" if ok else "FAIL", label, got, want))
        return ok

    def status(self):
        """What a client actually receives. Not what the config says."""
        try:
            with urllib.request.urlopen(self.base, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def run(self):
        prefix = Path(tempfile.mkdtemp(prefix="aiops-nginx-"))
        conf_dir = prefix / "agent.d"
        conf_dir.mkdir()
        (prefix / "logs").mkdir()
        (prefix / "www").mkdir()
        (prefix / "www" / "index.html").write_text("ok")

        print("adapter writes %s and %s, geo var %s"
              % (BLOCK_FILE, GEO_FILE, GEO_VAR))

        # nginx must be able to include both before anything has been
        # blocked, so they start empty and valid.
        (conf_dir / BLOCK_FILE).write_text("")
        (conf_dir / GEO_FILE).write_text(
            "geo $binary_remote_addr " + GEO_VAR + " {\n    default 0;\n}\n")

        conf = prefix / "nginx.conf"
        conf.write_text(_CONF.format(p=prefix, d=conf_dir, geo=GEO_FILE,
                                     block=BLOCK_FILE, port=self.port))

        nginx = ["nginx", "-p", str(prefix), "-c", str(conf)]
        reload_cmd = nginx + ["-s", "reload"]

        probe = subprocess.run(nginx + ["-t"], capture_output=True, text=True)
        if probe.returncode != 0:
            print("nginx refused the generated config:\n" + probe.stderr)
            shutil.rmtree(prefix, ignore_errors=True)
            return 1

        subprocess.run(nginx, capture_output=True, text=True, check=True)
        time.sleep(0.6)

        try:
            adapter = NginxAdapter(config_dir=conf_dir, reload_cmd=reload_cmd)

            print("\n1. nginx is up and the adapter can see its config dir")
            self.check("adapter.is_available()", adapter.is_available(), True)
            self.check("GET / before any block", self.status(), 200)

            print("\n2. a real tier-2 block, applied through a real reload")
            self.check("adapter.block() reported success",
                       adapter.block("127.0.0.1", tier=2,
                                     expires_at=int(time.time()) + 3600), True)
            self.check("deny line on disk",
                       "deny 127.0.0.1;" in (conf_dir / BLOCK_FILE).read_text(),
                       True)
            time.sleep(0.6)
            self.check("GET / while blocked", self.status(), 403)

            print("\n3. unblocking actually lets the client back in")
            self.check("adapter.unblock() reported success",
                       adapter.unblock("127.0.0.1"), True)
            time.sleep(0.6)
            self.check("GET / after unblock", self.status(), 200)

            print("\n4. a tier-1 entry, and nginx still accepts the config")
            adapter.block("203.0.113.9", tier=1,
                          expires_at=int(time.time()) + 300)
            geo = (conf_dir / GEO_FILE).read_text()
            print("    --- %s ---" % GEO_FILE)
            for line in geo.splitlines():
                print("    |" + line)
            self.check("geo entry on disk", "203.0.113.9 1;" in geo, True)
            again = subprocess.run(nginx + ["-t"], capture_output=True,
                                   text=True)
            if again.returncode != 0:
                print(again.stderr)
            self.check("nginx -t after a tier-1 write", again.returncode, 0)
            self.check("reload after a tier-1 write",
                       subprocess.run(reload_cmd,
                                      capture_output=True).returncode, 0)

            print("\n5. the adapter refuses anything that is not an IP")
            try:
                adapter.block("1.2.3.4; root /etc", tier=2, expires_at=0)
                self.check("injection attempt refused", False, True)
            except ValueError:
                self.check("injection attempt refused", True, True)
            self.check("nothing was written for it",
                       "root /etc" in (conf_dir / BLOCK_FILE).read_text(),
                       False)
        finally:
            subprocess.run(nginx + ["-s", "quit"], capture_output=True)
            time.sleep(0.4)
            shutil.rmtree(prefix, ignore_errors=True)

        failed = [r for r in self.results if not r[0]]
        print("\n=== REAL NGINX DRILL: %d/%d passed ==="
              % (len(self.results) - len(failed), len(self.results)))
        for ok, label in failed:
            print("  failed: " + label)
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099,
                        help="loopback port for the throwaway nginx")
    args = parser.parse_args()

    if shutil.which("nginx") is None:
        print("nginx is not on PATH; this drill needs a real one.\n"
              "On a Windows checkout run it from WSL:\n"
              "  wsl python3 scripts/nginx-enforcement-drill.py")
        return 2
    return Drill(args.port).run()


if __name__ == "__main__":
    raise SystemExit(main())
