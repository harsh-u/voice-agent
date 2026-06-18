from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DB
    database_url: str = "postgresql+asyncpg://voxscope:voxscope@localhost:5432/voxscope"

    # Auth (JWT)
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 1440
    jwt_refresh_expire_days: int = 30

    # API keys (ingest credentials)
    api_key_prefix: str = "vsk_"

    # Sampling defaults
    default_sample_rate: float = 1.0
    default_slow_threshold_ms: float = 800.0

    # Retention
    raw_span_retention_days: int = 7
    rollup_retention_days: int = 90
    trace_idle_seconds: int = 600

    # Background task intervals
    rollup_interval_seconds: int = 60
    retention_interval_seconds: int = 3600

    # Ingest limits
    max_spans_per_batch: int = 1000

    # CORS — comma-separated origins
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
