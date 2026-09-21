"""The scheduled probe: the thing that finally polls /ready.

Until this existed, nothing watched the deployment. /health and /ready answered
anyone who asked, and nobody asked; the free-tier ceiling flag was visible only
to someone who happened to open the Control Platform. The retrospective carried
both as open.

The probe calls /ready THROUGH CloudFront - the path a real agent takes - so it
catches an edge failure as well as an application one, and it reads the day's
usage ratio. It reports both as CloudWatch metrics via the Embedded Metric
Format: a structured log line that CloudWatch turns into metrics, with no
PutMetricData call and no extra IAM permission to get wrong.
"""
import io
import json
import urllib.error

from services.backend import probe_handler
from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _DAILY_REQUEST_CEILING, _today


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(status):
    def open_(req, timeout=None):
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(b""))
        return _Resp(status)
    return open_


def _unreachable(req, timeout=None):
    raise urllib.error.URLError("connection refused")


def _emf_line(capsys) -> dict:
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert len(lines) == 1, f"expected exactly one EMF line, got {lines}"
    return json.loads(lines[0])


def test_ready_endpoint_answering_200_is_reported_as_success(dynamo_resource, capsys):
    create_all_tables(dynamo_resource)
    probe_handler.run_probe("https://edge.example", dynamo_resource, opener=_opener(200))
    assert _emf_line(capsys)["ReadyProbeSuccess"] == 1


def test_a_503_from_ready_is_reported_as_failure_not_raised(dynamo_resource, capsys):
    """The probe must keep its own health separate from the service's. If it
    raised on a 503, the probe's Lambda Errors metric would mean both "the
    service is down" and "the probe has a bug", and nobody could tell which."""
    create_all_tables(dynamo_resource)
    probe_handler.run_probe("https://edge.example", dynamo_resource, opener=_opener(503))
    assert _emf_line(capsys)["ReadyProbeSuccess"] == 0


def test_an_unreachable_edge_is_a_failure_too(dynamo_resource, capsys):
    create_all_tables(dynamo_resource)
    probe_handler.run_probe("https://edge.example", dynamo_resource, opener=_unreachable)
    assert _emf_line(capsys)["ReadyProbeSuccess"] == 0


def test_daily_usage_ratio_is_reported_against_the_free_tier_ceiling(dynamo_resource, capsys):
    """The ceiling flag used to be visible only on the Control Platform page.
    As a metric it can drive an alarm at the same 80% the dashboard warns at."""
    create_all_tables(dynamo_resource)
    half = int(_DAILY_REQUEST_CEILING / 2)
    UsageCountersTable(dynamo_resource).put(date=_today(), total_requests=half,
                                            estimated_gb_seconds=0.0)
    probe_handler.run_probe("https://edge.example", dynamo_resource, opener=_opener(200))
    ratio = _emf_line(capsys)["DailyUsageRatio"]
    assert 0.49 < ratio < 0.51


def test_emf_line_declares_both_metrics_in_one_namespace(dynamo_resource, capsys):
    """If the _aws block is malformed CloudWatch silently ingests a log line and
    creates no metric - and an alarm on a metric that never exists never fires."""
    create_all_tables(dynamo_resource)
    probe_handler.run_probe("https://edge.example", dynamo_resource, opener=_opener(200))
    emf = _emf_line(capsys)
    declared = emf["_aws"]["CloudWatchMetrics"][0]
    assert declared["Namespace"] == probe_handler.METRIC_NAMESPACE
    assert {m["Name"] for m in declared["Metrics"]} == {"ReadyProbeSuccess", "DailyUsageRatio"}
    for dimension_set in declared["Dimensions"]:
        for name in dimension_set:
            assert name in emf, f"dimension {name} declared but has no value"
    assert isinstance(emf["_aws"]["Timestamp"], int)


def test_handler_return_value_survives_json_serialisation(dynamo_resource, monkeypatch, capsys):
    """The lesson from the retrain Lambda, which failed with Runtime.MarshalError
    on every run: the runtime json-encodes whatever a handler returns."""
    create_all_tables(dynamo_resource)
    monkeypatch.setenv("PROBE_TARGET_URL", "https://edge.example")
    monkeypatch.setattr(probe_handler, "get_dynamo_resource", lambda: dynamo_resource)
    monkeypatch.setattr(probe_handler, "_default_opener", _opener(200))

    result = probe_handler.handler({"source": "aws.events"}, None)
    decoded = json.loads(json.dumps(result))
    assert decoded["ready"] is True
