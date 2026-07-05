import logging
from enum import Enum
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED   = "closed"    # bình thường, request đi qua
    OPEN     = "open"      # lỗi nhiều, chặn request
    HALF_OPEN = "half_open" # thử lại sau timeout


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        name: str = "default",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.name              = name
        self.failure_count     = 0
        self.state             = CircuitState.CLOSED
        self.last_failure_time: datetime | None = None

    def _should_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.last_failure_time is None:
                return False
            elapsed = (
                datetime.now(timezone.utc) - self.last_failure_time
            ).total_seconds()
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit breaker [%s] entering HALF_OPEN state",
                    self.name,
                )
                return True
            return False

        return True

    def _on_success(self) -> None:
        self.failure_count = 0
        if self.state != CircuitState.CLOSED:
            logger.info(
                "Circuit breaker [%s] back to CLOSED state",
                self.name,
            )
        self.state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self.failure_count    += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker [%s] OPEN after %d failures",
                self.name,
                self.failure_count,
            )

    async def call(
        self,
        url: str,
        payload: dict,
        headers: dict,
        timeout: float = 5.0,
    ) -> bool:
        if not self._should_attempt():
            logger.warning(
                "Circuit breaker [%s] OPEN — skipping call to %s",
                self.name,
                url,
            )
            return False

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 200:
                    self._on_success()
                    return True
                else:
                    logger.error(
                        "Circuit breaker [%s] request failed: status=%d",
                        self.name,
                        response.status_code,
                    )
                    self._on_failure()
                    return False

        except httpx.RequestError as e:
            logger.error(
                "Circuit breaker [%s] request error: %s",
                self.name, e,
            )
            self._on_failure()
            return False


worker_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30,
    name="worker-orchestrator",
)
