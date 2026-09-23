"""The shipped nginx example and the adapter must agree, or a customer's
nginx breaks in a way nothing here would notice.

The coupling is exact and easy to sever by accident: config/nginx/nginx.conf
builds its Tier 1 rate limit on `$agent_rate_limited`, and nginx refuses to
start when a `map` references a variable no `geo` block defines. Renaming that
variable in nginx_adapter.py is a one-word change that would leave every
customer's nginx failing to come up on the next agent write.
"""
from pathlib import Path

from services.agent.enforcer.nginx_adapter import _GEO_VAR, NginxAdapter

_EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "config" / "nginx"


def test_seed_geo_file_is_byte_identical_to_what_the_adapter_writes(tmp_path):
    """The seed exists so nginx can start before the agent has ever enforced
    anything. If it drifts from the adapter's output, the first enforcement
    silently changes the file's shape."""
    adapter = NginxAdapter(config_dir=tmp_path)
    adapter._write_ratelimit_file(set())
    written = (tmp_path / "aiops-agent-geo.conf").read_text()

    seed = (_EXAMPLE_DIR / "aiops-agent-geo.conf").read_text()
    assert seed == written


def test_example_nginx_conf_uses_the_variable_the_adapter_defines():
    conf = (_EXAMPLE_DIR / "nginx.conf").read_text()
    assert _GEO_VAR in conf, f"{_GEO_VAR} is what the adapter emits; nginx.conf must consume it"


def test_example_nginx_conf_includes_the_agents_files_before_using_them():
    """Wildcard include, and it must come before the map that needs the geo
    block — nginx parses top to bottom."""
    conf = (_EXAMPLE_DIR / "nginx.conf").read_text()
    include_at = conf.index("include /etc/nginx/conf.d/aiops-agent-*.conf;")
    map_at = conf.index(f"map {_GEO_VAR}")
    assert include_at < map_at


def test_example_access_log_carries_every_field_the_collector_needs():
    """The seven features are computed from these. A log_format missing
    $request_time or $body_bytes_sent yields vectors the model never saw."""
    conf = (_EXAMPLE_DIR / "nginx.conf").read_text()
    log_format = conf[conf.index("log_format aiops"):conf.index("access_log")]
    for field in ("$remote_addr", "$request", "$status", "$body_bytes_sent",
                  "$request_time", "$http_user_agent"):
        assert field in log_format, f"{field} missing from the example log_format"


def test_the_example_log_format_emits_exactly_what_the_reader_needs():
    """The two ends of the same pipe, and they had never matched.

    The shipped format emitted $time_local and one "$request" field holding
    method, URI and protocol together, while the backend schema wants
    time_iso8601 and a separate method and URI. A customer following this
    file produced a log the product could not parse into its own schema, and
    nothing here would have noticed, because nothing read the log at all.
    """
    from services.agent.logsource import FIELDS

    conf = (_EXAMPLE_DIR / "nginx.conf").read_text()
    for field in FIELDS:
        assert f'"{field}"' in conf, f"the example log_format never emits {field}"


def test_the_example_log_format_escapes_its_values():
    """Without escape=json a request URI carrying a quote or a newline
    breaks the line, and the reader drops every request in it."""
    conf = (_EXAMPLE_DIR / "nginx.conf").read_text()
    assert "log_format aiops escape=json" in conf
