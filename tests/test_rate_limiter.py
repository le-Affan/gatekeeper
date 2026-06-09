import pytest

from src.middleware.rate_limiter import RateLimiter
from src.models import MiddlewareResult
from src.storage.in_memory import InMemoryStorage


def _make_rl(store, limit=3, window_seconds=60):
    return RateLimiter(
        algorithm="sliding_window",
        api_key_headers=[],
        storage=store,
        limit=limit,
        window_seconds=window_seconds,
    )


async def test_request_within_limit(store, make_context):
    rl = _make_rl(store, limit=3)
    ctx = make_context()
    result = await rl.process(ctx)
    assert result == MiddlewareResult.PASS
    assert ctx.abort_response is None


async def test_request_beyond_limit_is_rejected(store, make_context):
    rl = _make_rl(store, limit=2)
    await rl.process(make_context())
    await rl.process(make_context())
    ctx = make_context()
    result = await rl.process(ctx)
    assert result == MiddlewareResult.ABORT
    assert ctx.abort_response is not None
    assert ctx.abort_response["status_code"] == 429


async def test_beyond_limit_sets_rate_limited_metadata(store, make_context):
    rl = _make_rl(store, limit=1)
    await rl.process(make_context())
    ctx = make_context()
    await rl.process(ctx)
    assert ctx.metadata.get("rate_limited") is True


async def test_rejected_response_has_retry_after_header(store, make_context):
    rl = _make_rl(store, limit=1, window_seconds=30)
    await rl.process(make_context())
    ctx = make_context()
    await rl.process(ctx)
    headers = ctx.abort_response["headers"]
    assert "Retry-After" in headers
    assert headers["Retry-After"] == "30"


async def test_rejected_response_has_ratelimit_limit_header(store, make_context):
    rl = _make_rl(store, limit=5)
    for _ in range(5):
        await rl.process(make_context())
    ctx = make_context()
    await rl.process(ctx)
    headers = ctx.abort_response["headers"]
    assert "X-RateLimit-Limit" in headers
    assert headers["X-RateLimit-Limit"] == "5"


async def test_different_clients_have_independent_limits(store, make_context):
    rl = _make_rl(store, limit=1)
    # exhaust client A
    await rl.process(make_context(client_ip="10.0.0.1"))
    ctx_a = make_context(client_ip="10.0.0.1")
    result_a = await rl.process(ctx_a)
    assert result_a == MiddlewareResult.ABORT

    # client B is unaffected
    ctx_b = make_context(client_ip="10.0.0.2")
    result_b = await rl.process(ctx_b)
    assert result_b == MiddlewareResult.PASS


async def test_sliding_window_expires(store, make_context, monkeypatch):
    fake_time = 1000.0

    def _fake_time():
        return fake_time

    monkeypatch.setattr("time.time", _fake_time)
    monkeypatch.setattr("src.storage.in_memory.time.time", _fake_time)

    rl = _make_rl(store, limit=2, window_seconds=60)

    # fill the window at t=1000
    await rl.process(make_context())
    await rl.process(make_context())
    ctx_blocked = make_context()
    result = await rl.process(ctx_blocked)
    assert result == MiddlewareResult.ABORT

    # advance time past the window
    fake_time = 1000.0 + 61.0

    # same client — old entries expired, should be allowed again
    ctx_after = make_context()
    result_after = await rl.process(ctx_after)
    assert result_after == MiddlewareResult.PASS


async def test_token_bucket_allows_within_capacity(store, make_context):
    rl = RateLimiter(
        algorithm="token_bucket",
        api_key_headers=[],
        storage=store,
        capacity=3,
        refill_rate=0.0,
    )
    for _ in range(3):
        result = await rl.process(make_context())
        assert result == MiddlewareResult.PASS


async def test_token_bucket_rejects_beyond_capacity(store, make_context):
    rl = RateLimiter(
        algorithm="token_bucket",
        api_key_headers=[],
        storage=store,
        capacity=2,
        refill_rate=0.0,
    )
    await rl.process(make_context())
    await rl.process(make_context())
    ctx = make_context()
    result = await rl.process(ctx)
    assert result == MiddlewareResult.ABORT
    assert ctx.abort_response["status_code"] == 429
