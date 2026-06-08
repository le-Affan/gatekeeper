import time
from typing import Any, Optional

from src.storage.base import Storage


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

    # need to see the Lua script before implementing
    async def execute_script(
        self, script: str, keys: list[str], args: list[Any]
    ) -> Any:
        raise NotImplementedError()
