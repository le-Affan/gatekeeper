from abc import ABC, abstractmethod
from typing import Any, Optional


class Storage(ABC):
    # method to get a value if it exists in the store.
    @abstractmethod
    async def get_value(self, key: str) -> Optional[Any]:
        pass

    # method to set a value with a TTL.
    @abstractmethod
    async def set_value(self, key: str, value: Any, expire_in: int) -> None:
        pass

    # method to increment a value if it exists else create the value in the store.
    # Return the value in both cases
    @abstractmethod
    async def increment_value(self, key: str, expire_in: int) -> int:
        pass

    # method to run a Lua script.
    # parameters:
    # 1) the script
    # 2) keys to be operated
    # 3) arguments which are input values you want to pass into the Lua script
    @abstractmethod
    async def execute_script(
        self, script: str, keys: list[str], args: list[Any]
    ) -> Any:
        pass
