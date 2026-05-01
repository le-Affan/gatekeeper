from abc import ABC, abstractmethod

from models import MiddlewareContext, MiddlewareResult


class Middleware(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        pass

    async def on_response(self, context: MiddlewareContext):
        pass
