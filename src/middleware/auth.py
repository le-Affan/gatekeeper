# NOTE: API keys must be stored in Redis as sha256 hashes, not raw values.
# Key format: auth:apikey:<hashlib.sha256(api_key.encode()).hexdigest()>
# Any seeding script, init container, or CLI that registers keys must hash first.
# Storing raw keys will cause all auth lookups to silently fail (404 → 403).

"""
Auth Middleware

Validates the X-API-Key header against keys registered in storage (Redis).
Supports optional auth (require_auth=False) where missing keys are allowed
through as unauthenticated requests, and mandatory auth where a missing or
invalid key is rejected.
"""
import hashlib
import logging

from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult

logger = logging.getLogger("gatekeeper.auth")

API_KEY_HEADER = "x-api-key"
REDIS_KEY_PREFIX = "auth:apikey"


class Auth(Middleware):
    def __init__(self, require_auth: bool, storage):
        self.require_auth = require_auth
        self.storage = storage

    @property
    def name(self) -> str:
        return "auth"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        api_key = dict(context.request.headers).get(API_KEY_HEADER)

        if not api_key:
            if self.require_auth:
                # No key supplied but this route requires one - reject with 401
                # (caller never authenticated at all).
                context.abort_response = {
                    "status_code": 401,
                    "headers": {"WWW-Authenticate": "ApiKey"},
                    "body": b"Unauthorized: API key required",
                }
                return MiddlewareResult.ABORT

            # No key, but auth is optional on this route - mark unauthenticated
            # and let the request continue down the chain.
            context.metadata["authenticated"] = False
            return MiddlewareResult.PASS

        # Key supplied - look it up in storage. Namespaced so it can't collide
        # with rate-limiter / circuit-breaker keys in the same Redis instance.
        hashed_api_key = hashlib.sha256(api_key.encode()).hexdigest()
        redis_key = f"{REDIS_KEY_PREFIX}:{hashed_api_key}"
        try:
            key_id = await self.storage.get_value(redis_key)
        except Exception:
            # Storage outage during lookup. Fail closed when auth is mandatory -
            # we must not admit an unverified key - otherwise let the request
            # through as unauthenticated (auth is optional on this route).
            logger.warning("auth storage lookup failed", exc_info=True)
            context.metadata["auth_error"] = True
            if self.require_auth:
                context.abort_response = {
                    "status_code": 503,
                    "headers": {"Retry-After": "5"},
                    "body": b"Service Unavailable: auth backend unavailable",
                }
                return MiddlewareResult.ABORT
            context.metadata["authenticated"] = False
            return MiddlewareResult.PASS

        if key_id is None:
            # Key was presented but isn't registered - caller authenticated
            # with an identity that doesn't exist, so 403 (not 401).
            context.abort_response = {
                "status_code": 403,
                "headers": {},
                "body": b"Forbidden: invalid API key",
            }
            return MiddlewareResult.ABORT

        # Valid key - mark authenticated and expose the key's ID for
        # downstream middleware/handlers (e.g. logging, per-key rate limits).
        context.metadata["authenticated"] = True
        context.metadata["key_id"] = key_id
        return MiddlewareResult.PASS
