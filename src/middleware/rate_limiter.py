from src.middleware.base import Middleware
from src.models import MiddlewareContext, MiddlewareResult


class RateLimiter(Middleware):
    def __init__(self, algorithm: str, api_key_headers : list[str]):
        self.algorithm = algorithm
        self.api_key_headers = api_key_headers

    @property
    def name(self) -> str:
        return "rate-limiter"

    async def process(self, context: MiddlewareContext) -> MiddlewareResult:
        identifier = None
        
        for header in self.api_key_headers:
            identifier = context.request.headers.get(header)

            if identifier:
                break

        if not identifier:
            identifier = context.request.client_ip

        
                
            



            
                