from abc import ABC, abstractmethod

from src.models import MiddlewareContext, MiddlewareResult


class Middleware(ABC):
    @property  # lets you access a method like its an attribute
    @abstractmethod
    def name(self) -> str:  # used to indentify the middleware
        pass

    @abstractmethod
    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        pass

    async def on_response(self, context: MiddlewareContext):
        pass
