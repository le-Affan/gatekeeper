import time
from tracemalloc import start
from types import BuiltinMethodType

import httpx

from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult


class ProxyMiddleware(Middleware):
    def __init__(self):
        self.client = httpx.AsyncClient(follow_redirects=False)

    @property
    def name(self) -> str:
        return "proxy"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        curr_request = context.request
        curr_route = context.route_config

        # remove prefix if the route_config demands it
        if curr_route.strip_prefix:
            request_path = curr_request.path[len(curr_route.path_prefix) :]
            final_upstream_URL = curr_route.upstream_URL + request_path
        else:
            final_upstream_URL = curr_route.upstream_URL

        # remove hop-by-hop headers defined by RFC 2616 (world-wide standard)
        client_headers = curr_request.headers

        HOP_BY_HOP = {
            "connection",
            "keep-alive",
            "transfer-encoding",
            "te",
            "trailers",
            "upgrade",
            "proxy-authenticate",
            "proxy-authorization",
        }

        forward_headers = {}

        for key, value in client_headers.items():
            if key.lower() not in HOP_BY_HOP:
                forward_headers[key] = value

        # add standard headers expected by the server
        forward_headers["X-Request-ID"] = curr_request.request_id
        forward_headers["X-Forwarded-For"] = curr_request.client_ip
        forward_headers["X-Forwarded-Proto"] = "https"

        start_time = time.perf_counter()

        response = await self.client.request(
            method=curr_request.method,
            url=final_upstream_URL,
            headers=forward_headers,
            content=curr_request.body,
        )

        end_time = time.perf_counter()

        response_time = (end_time - start_time) * 1000
