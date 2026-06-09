import logging
import time

import pytest

from src.middleware import MiddlewareChain
from src.middleware.auth import Auth
from src.middleware.base import Middleware
from src.middleware.circuit_breaker import CircuitBreaker
from src.middleware.logger import Logger
from src.middleware.rate_limiter import RateLimiter
from src.models import MiddlewareContext, MiddlewareResult, ProxyResponse
from src.storage.in_memory import InMemoryStorage


class StubProxy(Middleware):
    @property
    def name(self) -> str:
        return "stub-proxy"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        context.response = ProxyResponse(
            request_id=context.request.request_id,
            headers={},
            body=b"OK",
            status_code=200,
            response_time=0.0,
        )
        return MiddlewareResult.PASS


def _percentile(sorted_values: list, p: float) -> float:
    idx = min(int(p * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[idx]


@pytest.fixture(autouse=True)
def suppress_access_log():
    logger = logging.getLogger("gatekeeper.access")
    original = logger.level
    logger.setLevel(logging.CRITICAL)
    yield
    logger.setLevel(original)


async def test_middleware_chain_overhead(make_context):
    storage = InMemoryStorage()
    chain = MiddlewareChain([
        Logger(),
        Auth(require_auth=False, storage=storage),
        RateLimiter(
            algorithm="token_bucket",
            api_key_headers=[],
            storage=storage,
            capacity=10000,
            refill_rate=10000.0,
        ),
        CircuitBreaker(
            failure_threshold=10000,
            window_seconds=60.0,
            recovery_timeout=30.0,
        ),
        StubProxy(),
    ])

    # warmup — discard results
    for _ in range(50):
        await chain.execute(make_context())

    # measured run
    latencies_ms = []
    for _ in range(1000):
        ctx = make_context()
        start = time.perf_counter()
        await chain.execute(ctx)
        end = time.perf_counter()
        latencies_ms.append((end - start) * 1000)

    latencies_ms.sort()
    p50 = _percentile(latencies_ms, 0.50)
    p99 = _percentile(latencies_ms, 0.99)

    print(f"\nMiddleware chain overhead — p50: {p50:.3f}ms  p99: {p99:.3f}ms")

    assert p99 < 5.0, f"p99 latency {p99:.3f}ms exceeded 5ms threshold"


async def test_rate_limiter_accuracy(make_context):
    storage = InMemoryStorage()
    rl = RateLimiter(
        algorithm="sliding_window",
        api_key_headers=[],
        storage=storage,
        limit=100,
        window_seconds=60,
    )

    allowed = 0
    rejected = 0

    for _ in range(200):
        ctx = make_context(client_ip="10.0.0.1")
        result = await rl.process(ctx)
        if result == MiddlewareResult.PASS:
            allowed += 1
        else:
            rejected += 1

    print(f"\nRate limiter accuracy — allowed: {allowed}  rejected: {rejected}")

    assert abs(allowed - 100) <= 1, (
        f"Expected ~100 allowed, got {allowed} (error margin exceeded 0.5%)"
    )
