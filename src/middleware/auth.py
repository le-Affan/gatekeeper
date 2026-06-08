"""
Auth Middleware

Validates the X-API-Key header against keys registered in storage (Redis).
Supports optional auth (require_auth=False) where missing keys are allowed
through as unauthenticated requests, and mandatory auth where a missing or
invalid key is rejected.
"""
from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult

API_KEY_HEADER = "X-API-Key"
REDIS_KEY_PREFIX = "auth:apikey"


class Auth(Middleware):
    def __init__(self, require_auth: bool, storage):
        self.require_auth = require_auth
        self.storage = storage

    @property
    def name(self) -> str:
        return "auth"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        api_key = context.request.headers.get(API_KEY_HEADER)

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
        redis_key = f"{REDIS_KEY_PREFIX}:{api_key}"
        key_id = await self.storage.get_value(redis_key)

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
