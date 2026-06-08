"""
Logger Middleware

Sits first in the chain. Records request start time in process(), then in
on_response() emits a single structured JSON log line summarizing the request:
identity, route, outcome, and timing - including rate-limit / circuit-breaker
context stamped into metadata by those middlewares.
"""
import json
import logging
import time

from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult

logger = logging.getLogger("gatekeeper.access")

START_TIME_METADATA_KEY = "request_start_time"


class Logger(Middleware):
    @property
    def name(self) -> str:
        return "logger"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        context.metadata[START_TIME_METADATA_KEY] = time.monotonic()
        return MiddlewareResult.PASS

    async def on_response(self, context: MiddlewareContext) -> None:
        start_time = context.metadata.get(START_TIME_METADATA_KEY)
        total_latency = time.monotonic() - start_time if start_time is not None else None

        if context.response is not None:
            status_code = context.response.status_code
            upstream_latency = context.response.response_time
        else:
            status_code = (context.abort_response or {}).get("status_code")
            upstream_latency = None

        record = {
            "request_id": context.request.request_id,
            "method": context.request.method,
            "path": context.request.path,
            "client_ip": context.request.client_ip,
            "route_id": context.route_config.route_id,
            "status_code": status_code,
            "upstream_latency": upstream_latency,
            "total_latency": total_latency,
            "circuit_state": context.metadata.get("circuit_state"),
            "rate_limited": context.metadata.get("rate_limited", False),
        }

        logger.info(json.dumps(record))
