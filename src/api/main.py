import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request, Response

from src.config.routes import load_routes, match_route
from src.middleware import MiddlewareChain
from src.middleware.auth import Auth
from src.middleware.circuit_breaker import CircuitBreaker
from src.middleware.logger import Logger
from src.middleware.rate_limiter import RateLimiter
from src.models import MiddlewareContext, ProxyRequest, RouteConfig
from src.proxy import ProxyMiddleware
from src.storage.in_memory import InMemoryStorage
from src.storage.redis_store import RedisStorage


def _build_storage():
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        return RedisStorage(redis_url)
    return InMemoryStorage()


def _build_registry(storage) -> Dict[str, Any]:
    return {
        "auth": Auth(require_auth=True, storage=storage),
        "rate-limiter": RateLimiter(
            algorithm=os.environ.get("RATE_LIMIT_ALGORITHM", "sliding_window"),
            api_key_headers=["x-api-key", "authorization", "api-key", "apikey"],
            storage=storage,
            capacity=int(os.environ.get("RATE_LIMIT_CAPACITY", "100")),
            refill_rate=float(os.environ.get("RATE_LIMIT_REFILL_RATE", "10.0")),
            limit=int(os.environ.get("RATE_LIMIT_LIMIT", "100")),
            window_seconds=int(os.environ.get("RATE_LIMIT_WINDOW", "60")),
        ),
        "circuit-breaker": CircuitBreaker(
            failure_threshold=int(os.environ.get("CB_FAILURE_THRESHOLD", "5")),
            window_seconds=float(os.environ.get("CB_WINDOW_SECONDS", "60.0")),
            recovery_timeout=float(os.environ.get("CB_RECOVERY_TIMEOUT", "30.0")),
            success_threshold=int(os.environ.get("CB_SUCCESS_THRESHOLD", "1")),
        ),
    }


def build_chain(route: RouteConfig, logger_mw: Logger, proxy_mw: ProxyMiddleware, registry: Dict[str, Any]) -> MiddlewareChain:
    chain = [logger_mw]

    for name in route.middleware_names:
        if name not in registry:
            raise ValueError(f"Unknown middleware '{name}' in route '{route.route_id}'")
        chain.append(registry[name])

    chain.append(proxy_mw)
    return MiddlewareChain(chain)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = _build_storage()
    registry = _build_registry(storage)
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

    app.state.storage = storage
    app.state.registry = registry
    app.state.logger_mw = logger_mw
    app.state.proxy_mw = proxy_mw
    app.state.routes = routes

    yield

    await proxy_mw.close()


app = FastAPI(lifespan=lifespan)

_SUPPORTED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@app.get("/gatekeeper/health")
async def health():
    return {"status": "ok"}


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
async def metrics():
    # TODO (Task 6.1): integrate analytics collector here
    return {"message": "metrics not yet implemented"}


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
        headers=dict(request.headers),
        body=body,
        client_ip=client_ip,
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

    if context.abort_response is not None:
        abort = context.abort_response
        return Response(
            content=abort.get("body", b""),
            status_code=abort.get("status_code", 500),
            headers=abort.get("headers", {}),
        )

    resp = context.response
    return Response(
        content=resp.body,
        status_code=resp.status_code,
        headers=resp.headers,
    )
