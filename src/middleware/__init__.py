from base import Middleware

from src.models import MiddlewareContext, MiddlewareResult


class MiddlewareChain:
    def __init__(self, middleware_list: list[Middleware]):
        self.middleware_list = middleware_list

    async def execute(self, context: MiddlewareContext):
        middleware_record = []

        for middleware in self.middleware_list:
            result = await middleware.process(context)
            middleware_record.append(middleware)

            if result == MiddlewareResult.ABORT:
                break

        for i in range(len(middleware_record) - 1, -1, -1):
            await middleware_record[i].on_response(context)

        return context
