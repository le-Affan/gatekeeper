import pytest

from src.middleware.circuit_breaker import CircuitBreaker
from src.models import CircuitState, MiddlewareResult, ProxyResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fail(cb: CircuitBreaker, ctx) -> MiddlewareResult:
    """Full request cycle that records a failure (no upstream response)."""
    result = await cb.process(ctx)
    # ctx.response stays None → CircuitBreaker.on_response records as failure
    await cb.on_response(ctx)
    return result


async def _succeed(cb: CircuitBreaker, ctx) -> MiddlewareResult:
    """Full request cycle that records a success (200 response)."""
    result = await cb.process(ctx)
    ctx.response = ProxyResponse(
        request_id=ctx.request.request_id,
        headers={},
        body=b"",
        status_code=200,
        response_time=1.0,
    )
    await cb.on_response(ctx)
    return result


def _state(cb: CircuitBreaker, route_id: str) -> CircuitState:
    return cb._routes[route_id].state


def _opened_at(cb: CircuitBreaker, route_id: str) -> float:
    return cb._routes[route_id].opened_at


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_circuit_opens_after_failure_threshold(make_context):
    cb = CircuitBreaker(failure_threshold=3, window_seconds=60, recovery_timeout=30)

    for _ in range(3):
        await _fail(cb, make_context())

    assert _state(cb, "test-route") == CircuitState.OPEN


async def test_open_circuit_returns_503(make_context):
    cb = CircuitBreaker(failure_threshold=2, window_seconds=60, recovery_timeout=30)

    for _ in range(2):
        await _fail(cb, make_context())

    ctx = make_context()
    result = await cb.process(ctx)

    assert result == MiddlewareResult.ABORT
    assert ctx.abort_response["status_code"] == 503
    assert "Retry-After" in ctx.abort_response["headers"]


async def test_recovery_timeout_transitions_to_half_open(make_context, monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    cb = CircuitBreaker(failure_threshold=2, window_seconds=60, recovery_timeout=30)

    for _ in range(2):
        await _fail(cb, make_context())

    assert _state(cb, "test-route") == CircuitState.OPEN

    # advance past recovery timeout
    fake_time[0] = 1031.0

    ctx = make_context()
    result = await cb.process(ctx)

    assert result == MiddlewareResult.PASS
    assert _state(cb, "test-route") == CircuitState.RECOVERY


async def test_half_open_allows_exactly_one_probe(make_context, monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    cb = CircuitBreaker(failure_threshold=2, window_seconds=60, recovery_timeout=30)

    for _ in range(2):
        await _fail(cb, make_context())

    fake_time[0] = 1031.0

    # first request after recovery timeout — probe elected
    probe_ctx = make_context()
    probe_result = await cb.process(probe_ctx)
    assert probe_result == MiddlewareResult.PASS

    # second request arrives before probe resolves (on_response not yet called)
    second_ctx = make_context()
    second_result = await cb.process(second_ctx)
    assert second_result == MiddlewareResult.ABORT
    assert second_ctx.abort_response["status_code"] == 503


async def test_successful_probe_closes_circuit(make_context, monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    cb = CircuitBreaker(failure_threshold=2, window_seconds=60, recovery_timeout=30, success_threshold=1)

    for _ in range(2):
        await _fail(cb, make_context())

    fake_time[0] = 1031.0

    # run probe with success
    await _succeed(cb, make_context())

    assert _state(cb, "test-route") == CircuitState.CLOSED

    # subsequent requests should pass
    ctx = make_context()
    result = await cb.process(ctx)
    assert result == MiddlewareResult.PASS


async def test_failed_probe_reopens_circuit_and_resets_timer(make_context, monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: fake_time[0])

    cb = CircuitBreaker(failure_threshold=2, window_seconds=60, recovery_timeout=30)

    for _ in range(2):
        await _fail(cb, make_context())

    fake_time[0] = 1031.0
    original_opened_at = _opened_at(cb, "test-route")

    # probe fails
    await _fail(cb, make_context())

    assert _state(cb, "test-route") == CircuitState.OPEN

    new_opened_at = _opened_at(cb, "test-route")
    assert new_opened_at > original_opened_at
    assert new_opened_at == pytest.approx(1031.0)
