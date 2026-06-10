import logging

from src.middleware.base import Middleware

from src.models import MiddlewareContext, MiddlewareResult

logger = logging.getLogger("gatekeeper.chain")


class MiddlewareChain:
    def __init__(self, middleware_list: list[Middleware]):
        self.middleware_list = middleware_list

    async def execute(self, context: MiddlewareContext):
        middleware_record = []

        try:
            for middleware in self.middleware_list:
                result = await middleware.process(context)
                middleware_record.append(middleware)

                if result not in (MiddlewareResult.PASS, MiddlewareResult.ABORT):
                    raise TypeError(
                        f"{middleware.name} returned {result!r}, expected MiddlewareResult"
                    )

                if result == MiddlewareResult.ABORT:
                    break
        except Exception:
            # An unhandled exception in process() must not skip the on_response
            # hooks below - otherwise circuit-breaker probe slots leak (permanent
            # per-route outage), failures go uncounted, and no access log/metric
            # is emitted. Synthesize a 500 so the gateway returns a real response
            # and downstream accounting still runs.
            logger.exception("middleware process() raised; synthesizing 500")
            context.metadata["chain_error"] = True
            if context.response is None and context.abort_response is None:
                context.abort_response = {
                    "status_code": 500,
                    "headers": {},
                    "body": b"Internal Server Error",
                }
        finally:
            # Always run on_response in reverse order for every middleware that
            # ran. One failing hook must not prevent the others from running.
            for i in range(len(middleware_record) - 1, -1, -1):
                try:
                    await middleware_record[i].on_response(context)
                except Exception:
                    logger.exception(
                        "on_response() raised in %s; continuing",
                        middleware_record[i].name,
                    )

        return context
