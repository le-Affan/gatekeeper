import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request, Response, WebSocket
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from src.analytics.collector import AnalyticsCollector, RequestRecord
from src.analytics.dashboard import DashboardManager
from src.config.routes import load_routes, match_route
from src.config.settings import GatekeeperSettings
from src.middleware import MiddlewareChain
from src.middleware.auth import Auth
from src.middleware.circuit_breaker import CircuitBreaker
from src.middleware.logger import Logger
from src.middleware.rate_limiter import RateLimiter
from src.models import MiddlewareContext, ProxyRequest, RouteConfig
from src.proxy import ProxyMiddleware
from src.storage.in_memory import InMemoryStorage
from src.storage.redis_store import RedisStorage

settings = GatekeeperSettings()
dashboard_manager = DashboardManager()


def _build_storage(s: GatekeeperSettings):
    if s.redis_url:
        return RedisStorage(s.redis_url)
    return InMemoryStorage()


def _build_registry(s: GatekeeperSettings, storage) -> Dict[str, Any]:
    return {
        "auth": Auth(require_auth=s.auth_require_auth, storage=storage),
        "rate-limiter": RateLimiter(
            algorithm=s.rate_limit_algorithm,
            api_key_headers=s.rate_limit_api_key_headers,
            storage=storage,
            capacity=s.rate_limit_capacity,
            refill_rate=s.rate_limit_refill_rate,
            limit=s.rate_limit_limit,
            window_seconds=s.rate_limit_window_seconds,
            trust_forwarded_for=s.rate_limit_trust_forwarded_for,
        ),
        "circuit-breaker": CircuitBreaker(
            failure_threshold=s.cb_failure_threshold,
            window_seconds=s.cb_window_seconds,
            recovery_timeout=s.cb_recovery_timeout,
            success_threshold=s.cb_success_threshold,
        ),
    }


def build_chain(
    route: RouteConfig,
    logger_mw: Logger,
    proxy_mw: ProxyMiddleware,
    registry: Dict[str, Any],
) -> MiddlewareChain:
    chain = [logger_mw]

    for name in route.middleware_names:
        if name not in registry:
            raise ValueError(
                f"Unknown middleware '{name}' in route '{route.route_id}'. "
                f"Known middleware: {sorted(registry.keys())}"
            )
        chain.append(registry[name])

    chain.append(proxy_mw)
    return MiddlewareChain(chain)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    storage = _build_storage(settings)
    registry = _build_registry(settings, storage)
    collector = AnalyticsCollector(settings.metrics_window_seconds)
    logger_mw = Logger()
    proxy_mw = ProxyMiddleware()
    routes = load_routes()

    for route in routes:
        for name in route.middleware_names:
            if name not in registry:
                raise ValueError(
                    f"Route '{route.route_id}' references unknown middleware '{name}'. "
                    f"Known middleware: {sorted(registry.keys())}"
                )

    app.state.collector = collector
    app.state.storage = storage
    app.state.registry = registry
    app.state.logger_mw = logger_mw
    app.state.proxy_mw = proxy_mw
    app.state.routes = routes

    yield

    await proxy_mw.close()


app = FastAPI(lifespan=lifespan)

_SUPPORTED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

REQUEST_COUNT = Counter(
    "gatekeeper_requests_total",
    "Total number of requests processed",
    ["route_id", "status_code"],
)
REQUEST_DURATION = Histogram(
    "gatekeeper_request_duration_seconds",
    "Total request duration in seconds",
    ["route_id"],
)
UPSTREAM_DURATION = Histogram(
    "gatekeeper_upstream_duration_seconds",
    "Upstream response duration in seconds",
    ["route_id"],
)
RATE_LIMITED_COUNT = Counter(
    "gatekeeper_rate_limited_total",
    "Total number of requests rejected by the rate limiter",
    ["route_id"],
)
CIRCUIT_OPEN = Gauge(
    "gatekeeper_circuit_open",
    "Whether the circuit breaker was open for the route on the last request (1=open, 0=closed)",
    ["route_id"],
)


@app.get("/gatekeeper/health")
async def health():
    # Liveness only: process is up and serving. Intentionally does NOT touch
    # dependencies - use /gatekeeper/ready for dependency health.
    return {"status": "ok"}


@app.get("/gatekeeper/ready")
async def ready(request: Request):
    # Readiness: verifies the configured storage backend is reachable. When no
    # redis_url is set the gateway uses in-memory storage and is always ready.
    if settings.redis_url:
        storage = getattr(request.app.state, "storage", None)
        if storage is None:
            return Response(
                content=b'{"status": "starting"}',
                status_code=503,
                media_type="application/json",
            )
        try:
            await storage.health_check()
        except Exception:
            logging.getLogger("gatekeeper.ready").warning(
                "readiness check failed: storage unreachable", exc_info=True
            )
            return Response(
                content=b'{"status": "unavailable", "storage": "unreachable"}',
                status_code=503,
                media_type="application/json",
            )
    return {"status": "ready"}


@app.get("/gatekeeper/routes")
async def routes_info(request: Request):
    route_list = [
        {
            "route_id": r.route_id,
            "path_prefix": r.path_prefix,
            "upstream_URL": r.upstream_URL,
            "timeout": r.timeout,
            "strip_prefix": r.strip_prefix,
            "middleware_names": r.middleware_names,
        }
        for r in request.app.state.routes
    ]
    return {"routes": route_list}


@app.get("/gatekeeper/metrics")
async def metrics(request: Request):
    return request.app.state.collector.get_summary()


@app.get("/metrics")
async def prometheus_metrics():
    if not settings.enable_prometheus:
        return Response(content=b"Not Found", status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/gatekeeper/dashboard")
async def dashboard(websocket: WebSocket):
    await dashboard_manager.connect(websocket, websocket.app.state.collector)


@app.api_route("/{path:path}", methods=_SUPPORTED_METHODS)
async def gateway(request: Request, path: str):
    full_path = "/" + path
    query = request.url.query
    if query:
        full_path = full_path + "?" + query

    body = await request.body()
    client_ip = request.client.host if request.client else "unknown"

    proxy_request = ProxyRequest(
        request_id=str(uuid.uuid4()),
        method=request.method,
        path=full_path,
        headers=list(request.headers.items()),
        body=body,
        client_ip=client_ip,
        metadata={"scheme": request.url.scheme},
    )

    matched_route = match_route(full_path, request.app.state.routes)
    if matched_route is None:
        return Response(content=b"Not Found", status_code=404)

    chain = build_chain(
        matched_route,
        request.app.state.logger_mw,
        request.app.state.proxy_mw,
        request.app.state.registry,
    )

    context = MiddlewareContext(request=proxy_request, route_config=matched_route)
    context = await chain.execute(context)

    if context.response is not None:
        status_code = context.response.status_code
        upstream_latency_ms = context.response.response_time
    elif context.abort_response is not None:
        status_code = context.abort_response.get("status_code", 500)
        upstream_latency_ms = 0.0
    else:
        status_code = 500
        upstream_latency_ms = 0.0

    route_id = str(matched_route.route_id)
    total_latency_ms = context.metadata.get("total_latency_ms") or 0.0
    rate_limited = context.metadata.get("rate_limited", False)
    circuit_open = context.metadata.get("circuit_state") == "open"

    request.app.state.collector.record(
        RequestRecord(
            timestamp=time.monotonic(),
            route_id=route_id,
            status_code=status_code,
            total_latency_ms=total_latency_ms,
            upstream_latency_ms=upstream_latency_ms,
            rate_limited=rate_limited,
            circuit_open=circuit_open,
        )
    )

    if settings.enable_prometheus:
        REQUEST_COUNT.labels(route_id=route_id, status_code=str(status_code)).inc()
        REQUEST_DURATION.labels(route_id=route_id).observe(total_latency_ms / 1000)
        UPSTREAM_DURATION.labels(route_id=route_id).observe(upstream_latency_ms / 1000)
        if rate_limited:
            RATE_LIMITED_COUNT.labels(route_id=route_id).inc()
        CIRCUIT_OPEN.labels(route_id=route_id).set(1 if circuit_open else 0)

    if context.abort_response is not None:
        abort = context.abort_response
        return Response(
            content=abort.get("body", b""),
            status_code=abort.get("status_code", 500),
            headers=abort.get("headers", {}),
        )

    resp = context.response
    response = Response(
        content=resp.body,
        status_code=resp.status_code,
    )
    # resp.headers is a list of (name, value) tuples; append each so repeated
    # headers (e.g. multiple Set-Cookie) all reach the client instead of being
    # collapsed by a dict.
    for name, value in resp.headers:
        response.headers.append(name, value)
    return response
