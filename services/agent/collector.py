import logging
import time
from dataclasses import dataclass

from services.agent.http_client import post_json

logger = logging.getLogger(__name__)

# `flush` clears its buffer only after a successful post, so a failed batch
# is retried with the next one. That is the right behaviour and it needs a
# ceiling: through a long backend outage a busy site would buffer every
# request it serves, and the first batch to exceed the backend's own
# 1000-record cap is refused with a 422 - permanently, because the buffer
# only ever grows from there. The agent would wedge itself shut on the day
# it is most needed, and take the machine's memory with it.
#
# Equal to TelemetryBatch's max_length, deliberately: one more than the
# backend accepts is a batch that can never be delivered.
MAX_BUFFERED_RECORDS = 1000


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
                 post_json_fn=None):
        self._url = f"{backend_url.rstrip('/')}/agent/v1/telemetry"
        self._agent_key = f"{tenant_id}.{api_key}"
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._post_json = post_json_fn
        self._buffer: list[LogRecord] = []
        self._last_flush = time.time()

    def add(self, record: LogRecord) -> dict | None:
        self._buffer.append(record)
        if len(self._buffer) > MAX_BUFFERED_RECORDS:
            # Oldest first. During an outage the recent requests are the
            # ones a decision still has any use for; a five-minute-old
            # bucket has already been served.
            dropped = len(self._buffer) - MAX_BUFFERED_RECORDS
            del self._buffer[:dropped]
            logger.warning("buffer full at %d records; dropped %d oldest. "
                           "The backend has been unreachable for a while.",
                           MAX_BUFFERED_RECORDS, dropped)
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
        # Resolved here rather than bound as a default argument. A default
        # captures the function at import time, so substituting
        # collector.post_json had no effect whatsoever and the CLI's own run
        # path could not be tested without opening a real socket.
        send = self._post_json or post_json
        result = send(self._url, payload, headers={"X-Agent-Key": self._agent_key})
        self._buffer.clear()
        self._last_flush = time.time()
        return result
