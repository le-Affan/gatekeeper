"""
Circuit Breaker Middleware

Trips per-route to OPEN after N failures occur within a rolling time window,
rejecting requests until a recovery timeout elapses. Then allows exactly one
probe request through (RECOVERY / half-open) to test the upstream; on probe
success(es) the circuit closes again, on probe failure it reopens.
"""
import time
from asyncio import Lock
from collections import deque
from dataclasses import dataclass, field

from src.middleware.base import Middleware
from src.models import CircuitState, MiddlewareContext, MiddlewareResult

PROBE_METADATA_KEY = "circuit_breaker_probe"


@dataclass
class _RouteState:
    state: CircuitState = CircuitState.CLOSED
    failure_timestamps: deque = field(default_factory=deque)  # rolling window of failure times
    opened_at: float = 0.0
    probe_in_flight: bool = False
    success_count: int = 0
    lock: Lock = field(default_factory=Lock)


class CircuitBreaker(Middleware):
    def __init__(
        self,
        failure_threshold: int,
        window_seconds: float,
        recovery_timeout: float,
        success_threshold: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._routes: dict[str, _RouteState] = {}

    @property
    def name(self) -> str:
        return "circuit-breaker"

    def _get_route_state(self, route_id: str) -> _RouteState:
        # setdefault is a single atomic dict operation - prevents two concurrent
        # first-requests for the same route from creating separate _RouteState
        # objects (which would mean separate Locks and broken mutual exclusion).
        return self._routes.setdefault(route_id, _RouteState())

    def _prune_failures(self, route_state: _RouteState, now: float) -> None:
        # Drop failure timestamps that have aged out of the rolling window.
        # Must run before threshold evaluation so stale failures never count.
        cutoff = now - self.window_seconds
        while route_state.failure_timestamps and route_state.failure_timestamps[0] < cutoff:
            route_state.failure_timestamps.popleft()

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        route_id = context.route_config.route_id
        route_state = self._get_route_state(route_id)
        now = time.monotonic()
        retry_after = int(self.recovery_timeout)

        async with route_state.lock:
            if route_state.state == CircuitState.CLOSED:
                # Normal operation: let the request through.
                allow = True

            elif route_state.state == CircuitState.OPEN:
                if now - route_state.opened_at >= self.recovery_timeout:
                    # OPEN -> RECOVERY: recovery timeout elapsed. This request
                    # becomes the single probe; claim the slot atomically while
                    # holding the lock so no other request can also become a probe.
                    route_state.state = CircuitState.RECOVERY
                    route_state.probe_in_flight = True
                    context.metadata[PROBE_METADATA_KEY] = True
                    allow = True
                else:
                    # Still within the open window: reject immediately, no upstream call.
                    allow = False
                    retry_after = max(0, int(self.recovery_timeout - (now - route_state.opened_at)))

            else:  # CircuitState.RECOVERY
                if not route_state.probe_in_flight:
                    # Prior probe resolved (e.g. succeeded but more probes are
                    # required to fully close). Elect this request as the next probe.
                    route_state.probe_in_flight = True
                    context.metadata[PROBE_METADATA_KEY] = True
                    allow = True
                else:
                    # A probe is already in flight; everyone else is rejected
                    # until it resolves in on_response.
                    allow = False
        # Lock released here. Upstream I/O (if allowed) happens outside the lock.

        # Expose the (post-transition) state so the logger can record it without
        # reaching into this middleware's private _routes map. Single stamp point
        # covers reject paths and probe-election paths alike, since route_state.state
        # already reflects any transition decided above.
        context.metadata["circuit_state"] = route_state.state.value

        if allow:
            return MiddlewareResult.PASS

        context.abort_response = {
            "status_code": 503,
            "headers": {"Retry-After": str(retry_after)},
            "body": b"Service Unavailable: circuit breaker open",
        }
        return MiddlewareResult.ABORT

    async def on_response(self, context: MiddlewareContext) -> None:
        # No upstream call was made (request was aborted by this breaker, or by
        # an earlier middleware) - nothing to record.
        if context.abort_response is not None and not context.metadata.get(PROBE_METADATA_KEY):
            return

        route_id = context.route_config.route_id
        route_state = self._get_route_state(route_id)
        now = time.monotonic()

        is_probe = context.metadata.get(PROBE_METADATA_KEY) is True
        failed = context.response is None or context.response.status_code >= 500

        async with route_state.lock:
            if failed:
                if is_probe:
                    # RECOVERY -> OPEN: probe failed, upstream still unhealthy.
                    # Reopen the circuit and reset recovery bookkeeping.
                    route_state.state = CircuitState.OPEN
                    route_state.opened_at = now
                    route_state.probe_in_flight = False
                    route_state.success_count = 0
                elif route_state.state == CircuitState.CLOSED:
                    # Record this failure in the rolling window, prune stale
                    # entries, and trip CLOSED -> OPEN if the threshold is met
                    # within the configured window.
                    route_state.failure_timestamps.append(now)
                    self._prune_failures(route_state, now)
                    if len(route_state.failure_timestamps) >= self.failure_threshold:
                        route_state.state = CircuitState.OPEN
                        route_state.opened_at = now
                        route_state.failure_timestamps.clear()
                # If state is OPEN and this wasn't a probe, the request was
                # aborted earlier (handled by the early-return above) - nothing to do.

            else:  # success
                if is_probe:
                    route_state.probe_in_flight = False
                    route_state.success_count += 1
                    if route_state.success_count >= self.success_threshold:
                        # RECOVERY -> CLOSED: enough consecutive probe successes,
                        # upstream considered healthy again. Reset all bookkeeping.
                        route_state.state = CircuitState.CLOSED
                        route_state.failure_timestamps.clear()
                        route_state.success_count = 0
                    # else: stay in RECOVERY: a subsequent request will be
                    # elected as the next probe (probe_in_flight is now False).
                elif route_state.state == CircuitState.CLOSED:
                    # Window-based counting ages out old failures naturally;
                    # a success requires no explicit reset here.
                    pass
