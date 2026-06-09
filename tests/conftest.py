import uuid

import pytest

from src.models import MiddlewareContext, ProxyRequest, RouteConfig
from src.storage.in_memory import InMemoryStorage


@pytest.fixture
def store():
    return InMemoryStorage()


@pytest.fixture
def route():
    return RouteConfig(
        route_id="test-route",
        path_prefix="/test",
        upstream_URL="http://localhost:19999",
        timeout=5.0,
        strip_prefix=True,
        middleware_names=[],
        metadata={},
    )


@pytest.fixture
def make_context(route):
    def _factory(client_ip="127.0.0.1", path="/test/resource", method="GET"):
        return MiddlewareContext(
            request=ProxyRequest(
                request_id=str(uuid.uuid4()),
                method=method,
                path=path,
                headers={},
                body=b"",
                client_ip=client_ip,
            ),
            route_config=route,
        )
    return _factory
