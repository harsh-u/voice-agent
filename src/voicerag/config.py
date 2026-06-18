from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DB
    database_url: str = "postgresql+asyncpg://localhost/voicerag"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_prefer_grpc: bool = True

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Embedding
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    enable_hybrid: bool = True
    sparse_model: str = "Qdrant/bm25"

    # Chunking
    chunk_target_tokens: int = 200
    chunk_overlap_tokens: int = 40

    # Retrieval
    default_top_k: int = 4
    max_top_k: int = 20
    query_cache_ttl_seconds: int = 600
    min_score_threshold: float = 0.30

    # Auth (JWT)
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 1440
    jwt_refresh_expire_days: int = 30

    # API keys
    api_key_prefix: str = "vrag_"
    api_key_cache_ttl_seconds: int = 300

    # Limits
    max_upload_mb: int = 25
    max_docs_free_plan: int = 200
    rate_limit_per_minute: int = 120

    # CORS — comma-separated origins; defaults to localhost dev only (no wildcard in prod)
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Optional answer generation
    enable_answer_endpoint: bool = False
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
