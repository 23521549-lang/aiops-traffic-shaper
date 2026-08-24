import time
from dataclasses import dataclass

from services.agent.http_client import post_json


@dataclass
class LogRecord:
    time_iso8601: str
    remote_addr: str
    request_method: str
    request_uri: str
    status: str
    body_bytes_sent: str
    request_time: str
    http_user_agent: str

    def to_dict(self) -> dict:
        return {
            "time_iso8601": self.time_iso8601, "remote_addr": self.remote_addr,
            "request_method": self.request_method, "request_uri": self.request_uri,
            "status": self.status, "body_bytes_sent": self.body_bytes_sent,
            "request_time": self.request_time, "http_user_agent": self.http_user_agent,
        }


class Collector:
    """Batches raw request metadata and forwards it to the backend — no
    feature computation, no sklearn, nothing but forwarding + batching.
    This is what keeps the agent genuinely 'thin' per ADR-002: all ML
    logic stays centralized in the backend Lambda."""

    def __init__(self, backend_url: str, tenant_id: str, api_key: str,
                 batch_size: int = 100, flush_interval_seconds: float = 5.0,
                 post_json_fn=post_json):
        self._url = f"{backend_url.rstrip('/')}/agent/v1/telemetry"
        self._agent_key = f"{tenant_id}.{api_key}"
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._post_json = post_json_fn
        self._buffer: list[LogRecord] = []
        self._last_flush = time.time()

    def add(self, record: LogRecord) -> dict | None:
        self._buffer.append(record)
        if len(self._buffer) >= self._batch_size:
            return self.flush()
        return None

    def maybe_flush_on_interval(self, now: float | None = None) -> dict | None:
        now = now if now is not None else time.time()
        if self._buffer and (now - self._last_flush) >= self._flush_interval:
            return self.flush()
        return None

    def flush(self) -> dict | None:
        if not self._buffer:
            return None
        payload = {"logs": [r.to_dict() for r in self._buffer]}
        result = self._post_json(self._url, payload, headers={"X-Agent-Key": self._agent_key})
        self._buffer.clear()
        self._last_flush = time.time()
        return result
