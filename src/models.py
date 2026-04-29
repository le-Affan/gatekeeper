from enum import Enum


class CircuitState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    RECOVERY = "recovery"

class MiddlewareResult(Enum):
    PASS = "pass"
    ABORT = "abort"

