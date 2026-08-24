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
