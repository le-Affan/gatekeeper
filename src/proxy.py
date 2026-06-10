import time
from urllib.parse import urlparse

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
        upstream_base = curr_route.upstream_URL.rstrip("/")
        if curr_route.strip_prefix:
            request_path = curr_request.path[len(curr_route.path_prefix) :]
            final_upstream_URL = upstream_base + request_path
        else:
            final_upstream_URL = upstream_base + curr_request.path

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
            # not hop-by-hop, but dropped so httpx recomputes the correct length
            # from the actual forwarded body (prevents Content-Length desync).
            "content-length",
        }

        # Headers the gateway injects itself - drop any inbound copy so we don't
        # emit duplicate, case-mismatched versions upstream. Host is overridden
        # below, so it's dropped here too. x-forwarded-for is handled separately
        # (appended, not replaced) so the upstream sees the full client chain.
        GATEWAY_OWNED = {"x-request-id", "x-forwarded-proto", "host"}

        forward_headers = []
        inbound_xff = None

        for key, value in client_headers:
            lowered = key.lower()
            if lowered == "x-forwarded-for":
                # Capture the existing chain; do not forward the raw inbound copy.
                inbound_xff = value if inbound_xff is None else f"{inbound_xff}, {value}"
                continue
            if lowered in HOP_BY_HOP or lowered in GATEWAY_OWNED:
                continue
            forward_headers.append((key, value))

        # Append this hop's immediate peer to the existing X-Forwarded-For chain
        # instead of clobbering it, so a downstream proxy/LB's recorded clients
        # are preserved.
        if inbound_xff:
            xff = f"{inbound_xff}, {curr_request.client_ip}"
        else:
            xff = curr_request.client_ip

        # add standard headers expected by the server
        forward_headers.append(("X-Request-ID", curr_request.request_id))
        forward_headers.append(("X-Forwarded-For", xff))
        forward_headers.append(("X-Forwarded-Proto", curr_request.metadata.get("scheme", "http")))

        # override Host with the upstream authority so upstream vhost routing
        # and TLS SNI target the upstream, not the gateway's own host.
        forward_headers.append(("host", urlparse(curr_route.upstream_URL).netloc))

        # actually sending the request upstream
        try:
            start_time = time.perf_counter()

            response = await self.client.request(
                method=curr_request.method,
                url=final_upstream_URL,
                headers=forward_headers,
                content=curr_request.body,
                timeout=curr_route.timeout,
            )

            # Read the body inside the try: httpx may raise DecodingError here
            # (malformed Content-Encoding from upstream), which is NOT a
            # TransportError and would otherwise escape process() uncaught.
            body = response.content
            end_time = time.perf_counter()

        except httpx.TimeoutException:
            context.metadata["upstream_attempted"] = True
            context.abort_response = {"status_code": 504, "headers": {}, "body": b"Request Timeout"}

            return MiddlewareResult.ABORT

        except httpx.ConnectError:
            context.metadata["upstream_attempted"] = True
            context.abort_response = {
                "status_code": 502,
                "headers": {},
                "body": b"Could Not Connect To Server",
            }

            return MiddlewareResult.ABORT

        except (httpx.HTTPError, httpx.InvalidURL, httpx.CookieConflict):
            # Catch-all for every remaining httpx failure: transport errors
            # (ReadError/WriteError/RemoteProtocolError/UnsupportedProtocol),
            # DecodingError, and the non-HTTPError siblings InvalidURL /
            # CookieConflict. None of these may propagate past process(), or
            # on_response, metrics, and circuit-breaker counting are all bypassed.
            context.metadata["upstream_attempted"] = True
            context.abort_response = {
                "status_code": 502,
                "headers": {},
                "body": b"Upstream Error",
            }

            return MiddlewareResult.ABORT

        # measuring response time
        curr_response_time = (end_time - start_time) * 1000

        # httpx already decoded the body, so the upstream's encoding/length
        # framing headers no longer apply - drop them before forwarding.
        # Use multi_items() and a list of tuples so repeated headers
        # (e.g. multiple Set-Cookie) are preserved instead of collapsed.
        response_headers = [
            (key, value)
            for key, value in response.headers.multi_items()
            if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        ]

        # building the response object
        context.response = ProxyResponse(
            request_id=curr_request.request_id,
            headers=response_headers,
            body=body,
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
