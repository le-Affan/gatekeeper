import time

import httpx

from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult, ProxyResponse


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
            final_upstream_URL = curr_route.upstream_URL + curr_request.path

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

        # actually sending the request upstream
        try:
            start_time = time.perf_counter()

            response = await self.client.request(
                method=curr_request.method,
                url=final_upstream_URL,
                headers=forward_headers,
                content=curr_request.body,
            )

        except httpx.TimeoutException:
            context.abort_response = {"status_code": 504, "detail": "Request Timeout"}

            return MiddlewareResult.ABORT

        except httpx.ConnectError:
            context.abort_response = {
                "status_code": 502,
                "detail": "Could Not Connect To Server",
            }

            return MiddlewareResult.ABORT

        # measuring response time
        end_time = time.perf_counter()
        curr_response_time = (end_time - start_time) * 1000

        # building the response object
        context.response = ProxyResponse(
            request_id=curr_request.request_id,
            headers=dict(response.headers),
            body=response.content,
            status_code=response.status_code,
            response_time=curr_response_time,
            from_cache=False,
        )

        return MiddlewareResult.PASS

    """
    This is kept as a separate method because the HTTP client is supposed
    to stay alive across many requests so connection pooling can work.

    If we closed the client inside process(), all pooled connections would
    be destroyed after every request, defeating the purpose of reusing them.

    This method is meant to be called once when the application shuts down,
    allowing the AsyncClient to gracefully close all open sockets and
    release network resources properly.
    """

    async def close(self):
        await self.client.aclose()
