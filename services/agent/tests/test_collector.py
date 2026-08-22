from services.agent.collector import Collector, LogRecord


def _record(ip="1.2.3.4"):
    return LogRecord(
        time_iso8601="2026-08-21T00:00:00Z", remote_addr=ip, request_method="GET",
        request_uri="/a", status="200", body_bytes_sent="512",
        request_time="0.05", http_user_agent="ua-1",
    )


def test_flush_sends_batched_logs_with_agent_key_header():
    captured = {}

    def fake_post(url, payload, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"received": len(payload["logs"])}

    collector = Collector("http://backend", "t-1", "secret", post_json_fn=fake_post)
    collector.add(_record("1.1.1.1"))
    collector.add(_record("2.2.2.2"))
    result = collector.flush()

    assert result == {"received": 2}
    assert captured["url"] == "http://backend/agent/v1/telemetry"
    assert captured["headers"] == {"X-Agent-Key": "t-1.secret"}
    assert len(captured["payload"]["logs"]) == 2


def test_flush_empty_buffer_is_noop():
    calls = []

    def fake_post(url, payload, headers=None):
        calls.append(payload)
        return {}

    collector = Collector("http://backend", "t-1", "secret", post_json_fn=fake_post)
    assert collector.flush() is None
    assert calls == []


def test_add_flushes_automatically_at_batch_size():
    calls = []

    def fake_post(url, payload, headers=None):
        calls.append(payload)
        return {}

    collector = Collector("http://backend", "t-1", "secret", batch_size=2, post_json_fn=fake_post)
    collector.add(_record())
    assert calls == []  # not yet at batch_size
    collector.add(_record())
    assert len(calls) == 1  # flushed automatically at batch_size=2


def test_maybe_flush_on_interval_respects_elapsed_time():
    calls = []

    def fake_post(url, payload, headers=None):
        calls.append(payload)
        return {}

    collector = Collector("http://backend", "t-1", "secret",
                           flush_interval_seconds=5.0, post_json_fn=fake_post)
    collector.add(_record())
    collector.maybe_flush_on_interval(now=collector._last_flush + 1.0)
    assert calls == []  # only 1s elapsed, interval is 5s
    collector.maybe_flush_on_interval(now=collector._last_flush + 6.0)
    assert len(calls) == 1
