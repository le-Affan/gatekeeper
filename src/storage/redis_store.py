from typing import Any, Optional

import redis.asyncio as aioredis

from src.storage.base import Storage


class RedisStorage(Storage):
    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url, decode_responses=True)

    async def get_value(self, key: str) -> Optional[Any]:
        return await self.redis.get(key)

    async def set_value(self, key: str, value: Any, expire_in: int) -> None:
        await self.redis.set(key, value, ex=expire_in)

    async def increment_value(self, key: str, expire_in: int) -> int:
        pipe = self.redis.pipeline(transaction=False)

        pipe.incr(key)
        pipe.expire(name=key, time=expire_in)

        res = await pipe.execute()

        return res[0]

    async def execute_script(
        self, script: str, keys: list[str], args: list[Any]
    ) -> Any:
        return await self.redis.eval(script, len(keys), *keys, *args)
