from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatekeeperSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Storage
    redis_url: Optional[str] = None

    # Gateway
    gateway_port: int = 8080
    log_level: str = "INFO"
    metrics_window_seconds: int = 60
    enable_prometheus: bool = False

    # Auth
    auth_require_auth: bool = False

    # Rate limiting
    rate_limit_algorithm: str = "sliding_window"
    rate_limit_api_key_headers: List[str] = ["x-api-key", "authorization", "api-key", "apikey"]
    rate_limit_capacity: int = 100
    rate_limit_refill_rate: float = 10.0
    rate_limit_limit: int = 100
    rate_limit_window_seconds: int = 60

    # Circuit breaker
    cb_failure_threshold: int = 5
    cb_window_seconds: float = 60.0
    cb_recovery_timeout: float = 30.0
    cb_success_threshold: int = 1
