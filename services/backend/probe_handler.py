"""Scheduled self-monitoring probe - a third Lambda, sharing the package.

Nothing polled the deployment before this. /health and /ready answered whoever
asked and nobody asked; the free-tier ceiling flag was visible only to someone
who opened the Control Platform. An EventBridge rule now runs this every five
minutes.

It checks two things and reports them as CloudWatch metrics through the
Embedded Metric Format - one structured log line that CloudWatch turns into
metrics on ingest. No PutMetricData call, so no extra IAM permission to get
wrong and no extra API request to pay for; two custom metrics of the ten the
Always-Free tier includes.

  ReadyProbeSuccess  1 when GET /ready through CloudFront answers 200, else 0.
                     Through CloudFront on purpose: that is the path a real
                     agent takes, so an edge failure shows up here too, not
                     only an application one.
  DailyUsageRatio    today's global request count over the free-tier daily
                     ceiling - the number the dashboard warns about at 0.8.

The probe never raises for a service failure. Its own Lambda Errors metric then
means "the probe is broken" and ReadyProbeSuccess means "the service is down",
and nobody has to guess which one fired.
"""
import json
import os
import time
import urllib.error
import urllib.request

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import _DAILY_REQUEST_CEILING, get_usage_report

METRIC_NAMESPACE = "AiopsTrafficShaper"
_SERVICE = "aiops-traffic-shaper"
_TIMEOUT_SECONDS = 10

_default_opener = urllib.request.urlopen


def _probe_ready(base_url: str, opener) -> bool:
    req = urllib.request.Request(f"{base_url.rstrip('/')}/ready", method="GET")
    try:
        with opener(req, timeout=_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        # HTTPError (a 503 from /ready) is a URLError subclass, so a not-ready
        # service and an unreachable edge both land here - and both are
        # exactly what this probe exists to report.
        return False


def _emf(ready: bool, usage_ratio: float) -> str:
    return json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["Service"]],
                "Metrics": [
                    {"Name": "ReadyProbeSuccess", "Unit": "Count"},
                    {"Name": "DailyUsageRatio", "Unit": "None"},
                ],
            }],
        },
        "Service": _SERVICE,
        "ReadyProbeSuccess": 1 if ready else 0,
        "DailyUsageRatio": usage_ratio,
    })


def run_probe(base_url: str, resource, opener=None) -> dict:
    ready = _probe_ready(base_url, opener or _default_opener)
    usage = get_usage_report(resource, None)
    ratio = round(usage.total_requests / _DAILY_REQUEST_CEILING, 6)
    print(_emf(ready, ratio))
    # Plain types only. The retrain Lambda failed with Runtime.MarshalError on
    # every run because it returned a dataclass; the runtime json-encodes this.
    return {"ready": ready, "daily_usage_ratio": ratio}


def handler(event, context):
    return run_probe(os.environ["PROBE_TARGET_URL"], get_dynamo_resource(),
                     opener=_default_opener)
