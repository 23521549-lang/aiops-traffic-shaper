from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.ai_engine.core.http_client import CircuitBreaker, CircuitState


def _mock_client(status_code: int | None = 200, raises: bool = False):
    mock_response = MagicMock()
    mock_response.status_code = status_code

    mock_client = AsyncMock()
    if raises:
        mock_client.post = AsyncMock(side_effect=httpx.RequestError("boom"))
    else:
        mock_client.post = AsyncMock(return_value=mock_response)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


class TestCircuitBreakerHappyPath:
    @pytest.mark.anyio
    async def test_successful_call_returns_true_and_stays_closed(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=200),
        ):
            result = await breaker.call("http://x/", {}, {})

        assert result is True
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.anyio
    async def test_non_200_response_counts_as_failure(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=500),
        ):
            result = await breaker.call("http://x/", {}, {})

        assert result is False
        assert breaker.failure_count == 1

    @pytest.mark.anyio
    async def test_request_error_counts_as_failure(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(raises=True),
        ):
            result = await breaker.call("http://x/", {}, {})

        assert result is False
        assert breaker.failure_count == 1


class TestCircuitBreakerStateMachine:
    @pytest.mark.anyio
    async def test_opens_after_reaching_failure_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=500),
        ):
            for _ in range(3):
                await breaker.call("http://x/", {}, {})

        assert breaker.state == CircuitState.OPEN

    @pytest.mark.anyio
    async def test_open_circuit_skips_call_without_reaching_httpx(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=500),
        ):
            await breaker.call("http://x/", {}, {})  # trips to OPEN

        assert breaker.state == CircuitState.OPEN

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient"
        ) as mock_client_cls:
            result = await breaker.call("http://x/", {}, {})
            mock_client_cls.assert_not_called()

        assert result is False

    @pytest.mark.anyio
    async def test_half_open_after_recovery_timeout_elapses(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now(timezone.utc) - timedelta(seconds=31)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=200),
        ):
            result = await breaker.call("http://x/", {}, {})

        assert result is True
        assert breaker.state == CircuitState.CLOSED  # success in half-open -> closed

    @pytest.mark.anyio
    async def test_failure_during_half_open_reopens_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now(timezone.utc) - timedelta(seconds=31)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient",
            return_value=_mock_client(status_code=500),
        ):
            result = await breaker.call("http://x/", {}, {})

        assert result is False
        assert breaker.state == CircuitState.OPEN

    @pytest.mark.anyio
    async def test_still_open_before_recovery_timeout_elapses(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = datetime.now(timezone.utc) - timedelta(seconds=5)

        with patch(
            "services.ai_engine.core.http_client.httpx.AsyncClient"
        ) as mock_client_cls:
            result = await breaker.call("http://x/", {}, {})
            mock_client_cls.assert_not_called()

        assert result is False
        assert breaker.state == CircuitState.OPEN
