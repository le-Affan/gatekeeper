from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult


class ProxyMiddleware(Middleware):
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

        forward_headers["X-Request-ID"] = curr_request.request_id
        forward_headers["X-Forwarded-For"] = curr_request.client_ip
        forward_headers["X-Forwarded-Proto"] = "https"
