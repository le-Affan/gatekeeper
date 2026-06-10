"""
Rate Limiter Middleware

The RateLimiter class is a custom middleware component that inherits from the Middleware base class.
It enforces per-client request limits using either a token bucket or sliding window algorithm,
with state stored atomically in Redis via Lua scripts.

Key Components:
- Constructor (__init__): Accepts and stores configuration parameters: algorithm, api_key_headers,
  storage, plus algorithm-specific tuning (capacity/refill_rate for token bucket,
  limit/window_seconds for sliding window).
- Property name: Returns "rate-limiter".
- Method process: Resolves a unique client identifier for rate limiting by checking API key headers
  and falling back to the client's IP address, then runs the configured algorithm's Lua script
  against Redis to decide whether the request is allowed.
"""
import hashlib
import logging
import time
import uuid

from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult
from src.storage.base import Storage

logger = logging.getLogger("gatekeeper.rate_limiter")

# Lua script for token bucket algorithm.
# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens)
# ARGV[2] = refill_rate (tokens per second)
# ARGV[3] = now (current timestamp, seconds, float)
# ARGV[4] = requested tokens (cost of this request)
# ARGV[5] = ttl (seconds, used to expire idle buckets)
TOKEN_BUCKET_SCRIPT = """
local bucket_key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

-- Use the Redis server clock as the single source of truth so multiple
-- gateway instances sharing this Redis cannot corrupt refill math via wall-clock
-- skew. ARGV[3] (the caller's clock) is ignored on the Redis path.
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local bucket = redis.call('HMGET', bucket_key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

-- refill based on elapsed time, capped at capacity
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', bucket_key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', bucket_key, ttl)

return allowed
"""

# Lua script for sliding window algorithm.
# KEYS[1] = sorted set key
# ARGV[1] = now (current timestamp, seconds, float)
# ARGV[2] = window (window size in seconds)
# ARGV[3] = limit (max requests allowed in the window)
# ARGV[4] = request_id (unique member id for this request)
# ARGV[5] = ttl (seconds, used to expire idle windows)
SLIDING_WINDOW_SCRIPT = """
local window_key = KEYS[1]
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]
local ttl = tonumber(ARGV[5])

-- Redis server clock is authoritative (see token-bucket script); ARGV[1] is
-- ignored on the Redis path so multi-instance clock skew cannot shift the window.
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

-- drop entries older than the window
redis.call('ZREMRANGEBYSCORE', window_key, '-inf', now - window)

local count = redis.call('ZCARD', window_key)

local allowed = 0
if count < limit then
    redis.call('ZADD', window_key, now, request_id)
    allowed = 1
end

redis.call('EXPIRE', window_key, ttl)

return allowed
"""


class RateLimiter(Middleware):
    def __init__(
        self,
        algorithm: str,
        api_key_headers: list[str],
        storage: Storage,
        capacity: int = 10,
        refill_rate: float = 1.0,
        limit: int = 10,
        window_seconds: int = 60,
        trust_forwarded_for: bool = False,
    ):
        self.storage = storage
        self.algorithm = algorithm
        self.api_key_headers = api_key_headers

        # token bucket config
        self.capacity = capacity
        self.refill_rate = refill_rate

        # sliding window config
        self.limit = limit
        self.window_seconds = window_seconds

        # When true, derive client identity from the left-most X-Forwarded-For
        # entry rather than the immediate peer IP. Required when the gateway sits
        # behind a trusted proxy/LB, otherwise every client behind that LB shares
        # one bucket. Default false (only trust XFF when the deployment guarantees
        # a sanitizing proxy in front).
        self.trust_forwarded_for = trust_forwarded_for

    @property
    def name(self) -> str:
        return "rate-limiter"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:

        # identify client API Key else fallback to IP Address
        request_headers = dict(context.request.headers)
        identifier = None
        for header in self.api_key_headers:
            identifier = request_headers.get(header)

            if identifier:
                break

        if not identifier:
            if self.trust_forwarded_for:
                # Left-most XFF entry is the original client when a trusted proxy
                # sits in front; fall back to the peer IP if the header is absent.
                xff = request_headers.get("x-forwarded-for")
                if xff:
                    identifier = xff.split(",")[0].strip()
            if not identifier:
                identifier = context.request.client_ip

        # creating namespaced key (namespaced so it can't collide with e.g. circuit-breaker keys).
        # identifier is hashed so raw API keys / tokens never appear in the Redis keyspace.
        hashed_identifier = hashlib.sha256(identifier.encode()).hexdigest()
        key = f"rate-limiter:{self.algorithm}:{hashed_identifier}"

        now = time.time()

        try:
            if self.algorithm == "token_bucket":
                # Floor the TTL so a bucket is never evicted before it would have
                # refilled to capacity (premature eviction resets to full = silent
                # under-limiting under low-refill / small-capacity configs).
                refill_ttl = int(self.capacity / self.refill_rate) + 1 if self.refill_rate > 0 else 60
                ttl = max(60, refill_ttl)
                allowed = await self.storage.execute_script(
                    TOKEN_BUCKET_SCRIPT,
                    [key],
                    [self.capacity, self.refill_rate, now, 1, ttl],
                )
            else:  # sliding_window
                ttl = self.window_seconds + 1
                request_id = f"{now}:{uuid.uuid4()}"
                allowed = await self.storage.execute_script(
                    SLIDING_WINDOW_SCRIPT,
                    [key],
                    [now, self.window_seconds, self.limit, request_id, ttl],
                )
        except Exception:
            # Fail open: a storage/Redis outage must not 500 every request. But
            # make it observable - a silent global disable of rate limiting is a
            # DoS exposure, so log it and flag the context for the access log.
            logger.warning("rate limiter storage error; failing open", exc_info=True)
            context.metadata["rate_limiter_error"] = True
            return MiddlewareResult.PASS

        if int(allowed) == 1:
            return MiddlewareResult.PASS

        # RFC 6585: 429 Too Many Requests should include Retry-After.
        retry_after = (
            int(1 / self.refill_rate) + 1
            if self.algorithm == "token_bucket" and self.refill_rate > 0
            else self.window_seconds
        )

        # Explicit flag so downstream middleware (e.g. logger) doesn't have to
        # infer "rate limited" from the status code, which is fragile.
        context.metadata["rate_limited"] = True

        context.abort_response = {
            "status_code": 429,
            "headers": {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(
                    self.capacity if self.algorithm == "token_bucket" else self.limit
                ),
                "X-RateLimit-Remaining": "0",
            },
            "body": b"Too Many Requests",
        }

        return MiddlewareResult.ABORT
