import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CircuitState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    RECOVERY = "recovery"


class MiddlewareResult(Enum):
    PASS = "pass"
    ABORT = "abort"


@dataclass
class ProxyRequest:
    request_id: str
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes
    client_ip: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProxyResponse:
    request_id: str
    headers: Dict[str, str]
    body: bytes
    status_code: int
    response_time: float
    from_cache: bool = False


@dataclass
class RouteConfig:
    route_id: str
    path_prefix: str
    upstream_URL: str
    timeout: float
    strip_prefix: bool = True
    middleware_names: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MiddlewareContext:
    request: ProxyRequest
    route_config: RouteConfig
    abort_response: Optional[Dict[str, Any]] = None
    response: Optional[ProxyResponse] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    