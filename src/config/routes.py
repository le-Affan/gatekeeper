from typing import Optional

import yaml

from src.models import RouteConfig


# function to load all the routes parsed from the YAML file.
# returns a list of all the loaded routes
def load_routes() -> list[RouteConfig]:

    # parse the YAML file to extract all routes
    with open("routes.yaml", "r") as f:
        routes = yaml.safe_load(f)["routes"]

    route_configs = []

    for route in routes:
        route_config = RouteConfig(
            route_id=route["route_id"],
            path_prefix=route["path_prefix"],
            upstream_URL=route["upstream_URL"],
            timeout=route["timeout"],
            strip_prefix=route["strip_prefix"],
            middleware_names=route["middleware_names"],
            metadata=route["metadata"]
            if route["metadata"]
            else {},  # IMPORTANT: optional metadata handling is a bit weak here.
        )
        route_configs.append(route_config)

    return route_configs


# function to match an incoming request path with loaded possible routes
def match_route(
    incoming_route: str, possible_paths: list[RouteConfig]
) -> Optional[RouteConfig]:

    curr_len = -1
    curr_config = None

    for x in possible_paths:
        path = x.path_prefix

        if incoming_route.startswith(path) and len(path) > curr_len:
            curr_len = len(path)
            curr_config = x

    return curr_config
