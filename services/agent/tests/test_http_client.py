import json

import pytest

from services.agent.http_client import BackendError, get_json, post_json


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeHTTPError(Exception):
    def __init__(self, code, body: bytes):
        self.code = code
        self._body = body

    def read(self):
        return self._body


def test_post_json_returns_parsed_body():
    captured = {}

    def fake_opener(req):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["data"] = json.loads(req.data)
        return _FakeResponse({"ok": True})

    result = post_json("http://backend/agent/v1/telemetry", {"logs": []},
                        headers={"X-Agent-Key": "t-1.key"}, opener=fake_opener)
    assert result == {"ok": True}
    assert captured["url"] == "http://backend/agent/v1/telemetry"
    assert captured["headers"]["X-agent-key"] == "t-1.key"  # urllib title-cases header names
    assert captured["data"] == {"logs": []}


def test_post_json_raises_backend_error_on_http_error():
    import io
    import urllib.error

    def fake_opener(req):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                      io.BytesIO(b"invalid agent key"))

    with pytest.raises(BackendError, match="401"):
        post_json("http://backend/x", {}, opener=fake_opener)


def test_get_json_returns_parsed_body():
    def fake_opener(req):
        assert req.get_method() == "GET"
        return _FakeResponse({"decisions": []})

    result = get_json("http://backend/agent/v1/decisions",
                       headers={"X-Agent-Key": "t-1.key"}, opener=fake_opener)
    assert result == {"decisions": []}


def test_post_json_sends_body_hash_for_cloudfront_oac():
    """ADR-005: the backend sits behind CloudFront, whose origin access control
    signs the request to the Lambda function URL but NOT the body. Lambda
    refuses unsigned payloads, so a POST without x-amz-content-sha256 dies at
    the edge with a 403 the application never sees - and the agent would report
    a backend outage that is really a missing header.

    Measured against the real deployment on 2026-09-21: without this header
    403, with it 401 plus a real JSON error from the app.
    """
    import hashlib

    captured = {}

    def fake_opener(req):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = req.data
        return _FakeResponse({"ok": True})

    payload = {"logs": [{"ip": "1.2.3.4"}]}
    post_json("http://backend/agent/v1/telemetry", payload, opener=fake_opener)

    expected = hashlib.sha256(captured["data"]).hexdigest()
    assert captured["headers"]["x-amz-content-sha256"] == expected


def test_body_hash_matches_the_exact_bytes_that_are_sent():
    """The hash has to cover the serialised bytes, not a re-serialisation of
    the same dict: json.dumps is not guaranteed to produce identical output
    twice across versions or flags, and a hash of different bytes is a 403."""
    import hashlib

    captured = {}

    def fake_opener(req):
        captured["data"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _FakeResponse({"ok": True})

    post_json("http://backend/x", {"b": 2, "a": 1}, opener=fake_opener)
    assert hashlib.sha256(captured["data"]).hexdigest() == captured["headers"]["x-amz-content-sha256"]
