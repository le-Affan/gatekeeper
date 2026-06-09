import time
from typing import Any, List, Optional

from src.storage.base import Storage

# Script identity markers — used to dispatch in execute_script without importing
# from rate_limiter (which would create a circular dependency).
_TOKEN_BUCKET_MARKER = "HMGET"
_SLIDING_WINDOW_MARKER = "ZREMRANGEBYSCORE"


class InMemoryStorage(Storage):
    def __init__(self):
        self.store = {}
        self.lifetime = {}

    async def get_value(self, key: str) -> Optional[Any]:
        if key in self.store and key in self.lifetime and time.time() > self.lifetime[key]:
            del self.store[key]
            del self.lifetime[key]
            return None
        return self.store.get(key)

    async def set_value(self, key: str, value: Any, expire_in: int) -> None:
        self.store[key] = value
        self.lifetime[key] = time.time() + expire_in

    # this is atomic since there is no 'await'
    async def increment_value(self, key: str, expire_in: int) -> int:
        if key not in self.store.keys() or time.time() > self.lifetime[key]:
            # we initialise to 1 as thats how Redis would initialise a new val.
            self.store[key] = 1
            self.lifetime[key] = time.time() + expire_in
            return 1

        else:
            self.store[key] += 1
            return self.store[key]

    async def execute_script(
        self, script: str, keys: List[str], args: List[Any]
    ) -> Any:
        if _TOKEN_BUCKET_MARKER in script:
            return self._token_bucket(keys[0], args)
        if _SLIDING_WINDOW_MARKER in script:
            return self._sliding_window(keys[0], args)
        raise NotImplementedError(f"Unknown script passed to InMemoryStorage.execute_script")

    def _token_bucket(self, key: str, args: List[Any]) -> int:
        capacity = float(args[0])
        refill_rate = float(args[1])
        now = float(args[2])
        requested = float(args[3])
        ttl = float(args[4])

        bucket = self.store.get(key)
        if bucket is None or time.time() > self.lifetime.get(key, 0):
            tokens = capacity
            last_refill = now
        else:
            tokens = bucket["tokens"]
            last_refill = bucket["last_refill"]

        elapsed = max(0.0, now - last_refill)
        tokens = min(capacity, tokens + elapsed * refill_rate)

        allowed = 0
        if tokens >= requested:
            tokens -= requested
            allowed = 1

        self.store[key] = {"tokens": tokens, "last_refill": now}
        self.lifetime[key] = time.time() + ttl

        return allowed

    def _sliding_window(self, key: str, args: List[Any]) -> int:
        now = float(args[0])
        window = float(args[1])
        limit = int(args[2])
        request_id = str(args[3])
        ttl = float(args[4])

        entries: List[tuple] = self.store.get(key) or []
        if time.time() > self.lifetime.get(key, 0):
            entries = []

        cutoff = now - window
        entries = [(score, member) for score, member in entries if score > cutoff]

        allowed = 0
        if len(entries) < limit:
            entries.append((now, request_id))
            allowed = 1

        self.store[key] = entries
        self.lifetime[key] = time.time() + ttl

        return allowed
