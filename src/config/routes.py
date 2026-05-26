import yaml

from src.models import RouteConfig

with open("routes.yaml", "r") as f:
    routes = yaml.safe_load(f)["routes"]


def load_routes() -> list[RouteConfig]:
    route_configs = []

    for route in routes:
        route_config = RouteConfig(
            route_id=route["route_id"],
            path_prefix=route["path_prefix"],
            upstream_URL=route["upstream_URL"],
            timeout=route["timeout"],
            strip_prefix=route["strip_prefix"],
            middleware_names=route["middleware_names"],
            metadata=route["metadata"] if route["metadata"] else {},
        )
        route_configs.append(route_config)

    return route_configs
